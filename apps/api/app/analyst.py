"""The analyst: one question in, one answer out.

This is the module every entry point talks to (the HTTP API, the eval runner, and later MCP, bots and schedules).
It hides the LangGraph pipeline, the shape of its state, the step labels shown while it works, and the record
of which model provider served each step.
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.agents.chart import ChartSpec
from app.agents.followup import Turn
from app.analysis.drivers import Drivers
from app.brief import Brief, database
from app.confidence import Confidence, ConfidenceModel, assess
from app.context import Context
from app.db import DataSource
from app.graph import build_graph
from app.llm import LLM

STEP_LABELS = {
    "followup": "Reading it with the earlier questions",
    "router": "Understanding the question",
    "context": "Looking up similar questions",
    "verified": "Using a verified query",
    "sql_agent": "Writing SQL",
    "validate": "Checking the query against the schema",
    "execute": "Running the query",
    "chart_agent": "Choosing a chart",
    "summary_agent": "Writing the insight",
    "drivers": "Finding what drove the change",
    "projection": "Working out what to forecast",
    "forecast": "Forecasting",
    "schema_answer": "Describing the data",
    "out_of_scope": "Done",
    "give_up": "Stopped",
    "cache": "Found a recent answer to the same question",
}


class Failure(BaseModel):
    """An attempt that failed validation or execution, or found nothing, and was fed back to the SQL agent."""

    # Responses always carry every field, so the OpenAPI schema marks them required for generated clients.
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    sql: str
    error: str
    stage: Literal["validate", "execute", "result"]


class Answer(BaseModel):
    """Everything the analyst returns for one question. New fields are only ever added, each with a default."""

    # Responses always carry every field, so the OpenAPI schema marks them required for generated clients.
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    question: str
    intent: Literal["data_query", "schema_question", "out_of_scope"] | None = None
    answer: str | None = Field(default=None, description="Plain-text reply for schema and out-of-scope questions.")
    sql: str | None = None
    sql_reasoning: str | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    truncated: bool = False
    chart: ChartSpec | None = None
    chart_source: Literal["llm", "fallback"] | None = None
    summary: str | None = None
    error: str | None = Field(default=None, description="Set when no working query could be built.")
    attempts: int = 0
    failures: list[Failure] = Field(default_factory=list)
    timings: dict[str, float] = Field(default_factory=dict)
    llm_calls: list[dict[str, Any]] = Field(default_factory=list)
    cached: bool = Field(default=False, description="Served from the answer cache, without running the pipeline.")
    verified_question: str | None = Field(
        default=None, description="Answered with the verified query for this question instead of writing new SQL.")
    assumptions: list[str] = Field(
        default_factory=list, description="How an ambiguous question was read, in plain words.")
    summary_verified: bool | None = Field(
        default=None, description="Whether every figure in the summary was found in the result (None: not checked).")
    standalone_question: str | None = Field(
        default=None, description="A follow-up question as it was understood with the earlier ones in its conversation.")
    analysis: Literal["drivers", "forecast"] | None = Field(
        default=None, description="drivers: a question about why a metric changed, answered by splitting the change "
                                  "by each dimension in code (the rows are the drivers, biggest first). forecast: "
                                  "the history the SQL returned and, after it, the periods forecast in code with "
                                  "their 80% range (lower, upper).")
    confidence: Confidence | None = Field(
        default=None, description="How the answer was reached and, once a model is trained, how likely it is to be "
                                  "right (app/confidence.py). Only for answered data questions.")

    def as_turn(self) -> Turn:
        """This answer as an earlier turn of its conversation, for reading the questions after it."""
        return Turn(question=self.standalone_question or self.question, sql=self.sql,
                    summary=self.summary or self.answer)

    @classmethod
    def from_state(cls, state: dict[str, Any], confidence: ConfidenceModel | None = None) -> "Answer":
        data_query = state.get("intent") == "data_query"
        return cls(
            question=state.get("asked") or state.get("question", ""),
            standalone_question=state.get("question") if state.get("asked") else None,
            intent=state.get("intent"),
            answer=state.get("answer"),
            sql=state.get("sql") if data_query else None,
            sql_reasoning=state.get("sql_reasoning"),
            columns=state.get("columns", []),
            rows=state.get("rows", []),
            truncated=state.get("truncated", False),
            chart=state.get("chart"),
            chart_source=state.get("chart_source"),
            summary=state.get("summary"),
            error=state.get("error"),
            attempts=state.get("sql_attempts", 0),
            failures=state.get("failures", []),
            timings=state.get("timings", {}),
            llm_calls=state.get("llm_calls", []),
            verified_question=state.get("verified_question") if data_query else None,
            assumptions=state.get("assumptions") or [],
            summary_verified=state.get("summary_verified"),
            analysis=state.get("analysis"),
            confidence=assess(state, confidence),
        )


class Cache(Protocol):
    """Somewhere to keep answers to questions asked before (see app/answer_cache.py)."""

    def get(self, question: str) -> Answer | None: ...

    def put(self, question: str, answer: Answer) -> None: ...


@dataclass(frozen=True)
class Step:
    """Emitted by `Analyst.stream` as each pipeline step finishes."""

    node: str
    label: str


@dataclass(frozen=True)
class Table:
    """Emitted by `Analyst.stream` as soon as the query has run (rows already masked)."""

    columns: list[str]
    rows: list[list[Any]]
    truncated: bool


@dataclass(frozen=True)
class Chart:
    """Emitted by `Analyst.stream` once the chart is chosen."""

    spec: ChartSpec


@dataclass(frozen=True)
class Token:
    """Part of the summary as it is written. `replace` means `text` replaces everything sent so far (a rewrite);
    otherwise it is added to it. The summary in `Done` is the final one either way."""

    text: str
    replace: bool = False


@dataclass(frozen=True)
class Done:
    """The last event of `Analyst.stream`."""

    answer: Answer


class Analyst:
    """Answers business questions over one data source.

    Construction introspects the data source's schema once, so build one Analyst per data source and reuse it.
    `brief` says what the data is (app.brief); without one it is the connected database's.
    Email addresses and phone numbers in results are always masked (see app/privacy.py).
    `ask` and `stream` raise `AllProvidersFailed` when no model provider can classify the question; failures
    later in the pipeline are reported inside the Answer instead.

    With a `cache`, a question asked before is answered from it (marked `cached`), and complete new answers are
    added to it. With a `context` (app.context), the SQL agent sees verified examples and the values a question
    names, and a verified query can answer a question outright. `check_results` and `check_summaries` turn on
    the checks in app.checks. With `drivers` (app.analysis.drivers), questions about why a metric changed are
    answered by driver analysis, and with `forecasting` questions about the future are forecast from their
    history. Each of these is off unless given. A `confidence` model (app.confidence) scores each data answer;
    without one, answers carry only the signals it would use.
    """

    def __init__(self, llm: LLM, source: DataSource, *, brief: Brief | None = None, max_attempts: int = 3,
                 rows_for_llm: int = 50, cache: Cache | None = None, context: Context | None = None,
                 check_results: bool = False, check_summaries: bool = False, drivers: Drivers | None = None,
                 forecasting: bool = False, confidence: ConfidenceModel | None = None):
        self._graph = build_graph(llm, source, max_attempts=max_attempts, rows_for_llm=rows_for_llm,
                                  brief=brief or database(), context=context, check_results=check_results,
                                  check_summaries=check_summaries, drivers=drivers, forecasting=forecasting)
        self._cache = cache
        self._confidence = confidence

    def ask(self, question: str, history: list[Turn] | None = None) -> Answer:
        """Answer one question. `history` holds the earlier turns of its conversation, oldest first: a follow-up
        is then rewritten to stand on its own before it is answered (see Answer.standalone_question)."""
        question = question.strip()
        if self._cache and not history and (hit := self._cache.get(question)):
            return hit
        answer = Answer.from_state(self._graph.invoke(self._input(question, history)), self._confidence)
        if self._cache:
            self._cache.put(*self._cache_entry(answer))
        return answer

    @staticmethod
    def _input(question: str, history: list[Turn] | None) -> dict[str, Any]:
        return {"question": question, "history": list(history)} if history else {"question": question}

    @staticmethod
    def _cache_entry(answer: Answer) -> tuple[str, Answer]:
        """A follow-up's answer is kept under the question it was understood as, so asking that directly finds it."""
        if not answer.standalone_question:
            return answer.question, answer
        return answer.standalone_question, answer.model_copy(
            update={"question": answer.standalone_question, "standalone_question": None})

    async def stream(self, question: str, history: list[Turn] | None = None
                     ) -> AsyncIterator[Step | Table | Chart | Token | Done]:
        """Events as the answer takes shape: a Step as each step finishes, the Table once the query has run, the
        Chart, the summary as Tokens, and finally Done with the whole Answer. `history` is as for `ask`."""
        question = question.strip()
        # The cache does blocking database calls, so keep them off the event loop.
        if self._cache and not history and (hit := await asyncio.to_thread(self._cache.get, question)):
            yield Step("cache", STEP_LABELS["cache"])
            if hit.intent == "data_query":
                yield Table(hit.columns, hit.rows, hit.truncated)
                if hit.chart:
                    yield Chart(hit.chart)
                if hit.summary:
                    yield Token(hit.summary, replace=True)
            yield Done(hit)
            return
        final: dict[str, Any] = {}
        modes = ["updates", "values", "custom"]
        async for mode, chunk in self._graph.astream(self._input(question, history), stream_mode=modes):
            if mode == "updates":
                for node, update in chunk.items():
                    yield Step(node, STEP_LABELS.get(node, node))
                    if node in ("execute", "drivers", "forecast") and update and "rows" in update:
                        yield Table(update["columns"], update["rows"], update["truncated"])
                    if node in ("chart_agent", "drivers", "forecast") and update and "chart" in update:
                        yield Chart(ChartSpec.model_validate(update["chart"]))
            elif mode == "custom":
                if "token" in chunk:
                    yield Token(chunk["token"])
                elif "summary" in chunk:
                    yield Token(chunk["summary"] or "", replace=True)
            else:
                final = chunk
        answer = Answer.from_state(final, self._confidence)
        if self._cache:
            await asyncio.to_thread(self._cache.put, *self._cache_entry(answer))
        yield Done(answer)
