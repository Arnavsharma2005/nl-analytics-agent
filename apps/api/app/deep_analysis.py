"""Deep Analysis: a report from one broad question, built from answers the analyst can check.

"How did Q4 go and where should we focus?" is too broad for one query. A planner breaks it into two to eight
specific questions. Each one goes through the analyst's own pipeline (SQL with its guard and repair loop, driver
analysis for "why" questions, forecasts for questions about the future), so every figure comes from a query that
ran. A writer then turns the answers into a short report, and each section cites the steps it draws on.

No figure is taken from the writer on trust. Each section's numbers are checked against the rows of the steps it
cites (app/checks.py), and a section that quotes a figure none of them hold is sent back once with the figures
named. If it still quotes one, the section is kept and flagged with the figures that could not be traced.

Each step is one full analyst answer, so a report costs up to eight answers' worth of model calls plus two: the API
limits how many a signed-in user can request in a day (see app/api_reports.py).
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.analyst import Analyst, Answer
from app.brief import Brief
from app.checks import unsupported_numbers
from app.llm import LLM

MAX_STEPS = 8
ROWS_SHOWN = 20  # rows of each step the writer sees
ROWS_CHECKED = 50  # rows of each step a section's figures are checked against


class PlannedStep(BaseModel):
    question: str = Field(description="One specific question a single query can answer, in plain words.")
    purpose: str = Field(description="What this step contributes to the report, in a few words.")


class Plan(BaseModel):
    steps: list[PlannedStep] = Field(description=f"Two to {MAX_STEPS} steps, in the order the report needs them.")


class DraftSection(BaseModel):
    heading: str
    text: str = Field(description="Two to five sentences of findings, quoting figures only from the cited steps.")
    steps: list[int] = Field(description="The numbers of the steps this section draws on, e.g. [1, 3].")


class Draft(BaseModel):
    title: str
    summary: str = Field(description="The answer to the broad question in two or three sentences, no figures.")
    sections: list[DraftSection]


class SectionV1(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    heading: str
    text: str
    steps: list[int] = Field(description="The steps (1-based) whose answers this section draws on.")
    unsupported: list[str] = Field(description="Figures in the text that none of its steps' results hold; empty "
                                               "when every figure was traced.")


class StepV1(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    question: str
    purpose: str
    answer: Answer


class ReportV1(BaseModel):
    """A finished Deep Analysis report."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    question: str
    title: str
    summary: str
    sections: list[SectionV1]
    steps: list[StepV1]


Progress = Callable[[Literal["planned", "step", "writing"], int, int], None]  # (stage, steps done, steps in all)


PLAN_SYSTEM = """You plan an analysis of {about} for a business user. Break their broad question into two to
{max_steps} specific questions, each answerable by one database query and each adding something different: the
headline figures first, then breakdowns, comparisons or trends that explain them.{extra} Use plain words a
manager would use; do not write SQL. The tables are: {tables}.{money}"""

WHY_EXTRA = " A step may ask why a metric rose or fell between two periods; it is then split by every dimension."
FORECAST_EXTRA = " A step may ask what a metric will be in coming periods; it is then forecast from its history."

WRITE_SYSTEM = """You write a short analytical report from the numbered steps below, each a question and the answer
the data gave. Write three to six sections. In each, state findings and cite the steps they come from by number.
Quote a figure only if it appears in a cited step's result or summary, written as it is there or rounded; never
estimate, project or invent one. Skip steps that failed. No advice beyond what the figures show.{money}"""

FIX = """Section "{heading}" quotes figures that are not in the results of the steps it cites: {figures}. Rewrite
only that section, quoting figures from its steps' results, or cite the step that holds them."""


@dataclass(frozen=True)
class Capabilities:
    why: bool = False
    forecast: bool = False


def plan(llm: LLM, question: str, brief: Brief, tables: list[str], can: Capabilities) -> list[PlannedStep]:
    extra = (WHY_EXTRA if can.why else "") + (FORECAST_EXTRA if can.forecast else "")
    system = PLAN_SYSTEM.format(about=brief.about, max_steps=MAX_STEPS, extra=extra, tables=", ".join(tables),
                                money=f"\n{brief.money}" if brief.money else "")
    steps = [s for s in llm.structured(Plan, system, question).steps if s.question.strip()]
    seen: set[str] = set()
    unique = [s for s in steps if not (s.question.lower() in seen or seen.add(s.question.lower()))]
    if not unique:
        raise ValueError("the planner returned no steps")
    return unique[:MAX_STEPS]


def _step_text(n: int, step: StepV1) -> str:
    a = step.answer
    if a.error or a.intent != "data_query":
        return f"Step {n}: {step.question}\nFailed: {a.error or a.answer or 'not answered'}"
    rows = "\n".join(" | ".join("" if v is None else str(v) for v in r) for r in a.rows[:ROWS_SHOWN])
    more = f"\n... {len(a.rows) - ROWS_SHOWN} more rows" if len(a.rows) > ROWS_SHOWN else ""
    return (f"Step {n}: {step.question}\nSummary: {a.summary or ''}\nResult ({len(a.rows)} rows):\n"
            f"{' | '.join(a.columns)}\n{rows}{more}")


def _answered(step: StepV1) -> bool:
    return step.answer.intent == "data_query" and not step.answer.error


def _unsupported(text: str, cited: list[StepV1]) -> list[str]:
    """Figures in `text` that no cited step's rows (or simple sums, differences and ratios of them) hold."""
    if not cited:
        return unsupported_numbers(text, [], 0)
    per_step = [set(unsupported_numbers(text, s.answer.rows[:ROWS_CHECKED], len(s.answer.rows))) for s in cited]
    return sorted(set.intersection(*per_step))


def _section(draft: DraftSection, steps: list[StepV1]) -> tuple[SectionV1, list[StepV1]]:
    numbers = sorted({n for n in draft.steps if 1 <= n <= len(steps) and _answered(steps[n - 1])})
    cited = [steps[n - 1] for n in numbers]
    return SectionV1(heading=draft.heading.strip(), text=draft.text.strip(), steps=numbers,
                     unsupported=_unsupported(draft.text, cited)), cited


def write(llm: LLM, question: str, steps: list[StepV1], brief: Brief) -> tuple[str, str, list[SectionV1]]:
    system = WRITE_SYSTEM.format(money=f"\n{brief.money}" if brief.money else "")
    user = f"Broad question: {question}\n\n" + "\n\n".join(_step_text(n, s) for n, s in enumerate(steps, 1))
    draft = llm.structured(Draft, system, user)
    sections = []
    for d in draft.sections:
        section, _ = _section(d, steps)
        if section.unsupported:  # one rewrite, told exactly which figures it could not trace
            fix = FIX.format(heading=section.heading, figures=", ".join(section.unsupported))
            try:
                again = llm.structured(DraftSection, system, f"{user}\n\nYour section:\n{d.model_dump_json()}\n\n{fix}")
                retry, _ = _section(again, steps)
                if len(retry.unsupported) < len(section.unsupported):
                    section = retry
            except Exception:  # noqa: BLE001 - the flagged first draft is still worth showing
                pass
        sections.append(section)
    # The summary should carry no figures; one that quotes a figure no step holds is dropped rather than shown.
    summary = "" if _unsupported(draft.summary, [s for s in steps if _answered(s)]) else draft.summary.strip()
    return draft.title.strip(), summary, [s for s in sections if s.text]


class DeepAnalysis:
    """Runs a report over one analyst. `analyst` answers each step (with whatever driver analysis, forecasting and
    checks it was built with; `can` says which of those the planner may use)."""

    def __init__(self, llm: LLM, analyst: Analyst, brief: Brief, tables: list[str],
                 can: Capabilities | None = None):
        self._llm, self._analyst, self._brief, self._tables = llm, analyst, brief, tables
        self._can = can or Capabilities()

    def run(self, question: str, progress: Progress | None = None) -> ReportV1:
        """The report. Raises if the plan or the write-up fails; a step that fails is kept, marked, and not cited."""
        report = progress or (lambda *_: None)
        planned = plan(self._llm, question, self._brief, self._tables, self._can)
        report("planned", 0, len(planned))
        steps = []
        for n, p in enumerate(planned, 1):
            try:
                answer = self._analyst.ask(p.question)
            except Exception as e:  # noqa: BLE001 - one step failing leaves the others worth reporting
                answer = Answer(question=p.question, intent="data_query", error=f"{type(e).__name__}: {e}")
            steps.append(StepV1(question=p.question, purpose=p.purpose, answer=_public(answer)))
            report("step", n, len(planned))
        if not any(_answered(s) for s in steps):
            raise ValueError("none of the steps could be answered")
        report("writing", len(steps), len(steps))
        title, summary, sections = write(self._llm, question, steps, self._brief)
        return ReportV1(question=question, title=title, summary=summary, sections=sections, steps=steps)


def _public(answer: Answer) -> Answer:
    """What a report keeps of an answer: what its reader sees, not which models answered or the failed tries."""
    return answer.model_copy(update={"llm_calls": [], "failures": [], "timings": {}})
