"""Reads a "why did it change?" question as a metric and two periods to compare (app/analysis/drivers.py does the
rest in code)."""

from datetime import date

from pydantic import BaseModel, Field

from app.analysis.drivers import Drivers, Period
from app.llm import LLM

SYSTEM = """You read a business question about why a metric changed, or what drove a rise or fall, and say which
metric and which two periods to compare. You do not answer it.

METRICS
{metrics}

The data runs from {first} to {last}.

BUSINESS RULES
{rules}

HOW TO CHOOSE THE PERIODS
- "after" is the period the question asks about; "before" is what it is compared with.
- Two periods named: use them ("Q3 vs Q2 2025": before Q2 2025, after Q3 2025).
- One period named: compare it with the period of the same length just before it, unless the question says
  year on year, versus last year or the same period last year: then use the same period a year earlier.
- No period named: compare the latest complete month in the data with the month before it.
- Quarters and months are calendar ones. Periods are inclusive of both dates and must lie within the data.
- Pick the metric the question is about; "sales" means revenue, "basket size" means average order value."""


class WhyReading(BaseModel):
    metric: str = Field(description="The id of one metric from the list.")
    before_start: date
    before_end: date
    after_start: date
    after_end: date


class Unreadable(ValueError):
    """The model's reading names a metric that isn't defined, or impossible periods."""


def read_why(llm: LLM, question: str, drivers: Drivers, first: date | str, last: date | str, rules: str
             ) -> tuple[str, Period, Period]:
    """(metric id, before, after) for a "why did it change?" question; `first` and `last` bound the data."""
    metrics = "\n".join(f"- {m.id}: {m.label}" + (f" ({m.description})" if m.description else "")
                        for m in drivers.metrics)
    system = SYSTEM.format(metrics=metrics, first=str(first), last=str(last), rules=rules or "(none)")
    out = llm.structured(WhyReading, system, f"Question: {question}", tier="fast")
    if out.metric not in {m.id for m in drivers.metrics}:
        raise Unreadable(f"the question isn't about a metric that can be split ({out.metric!r})")
    try:
        before = Period(start=out.before_start, end=out.before_end)
        after = Period(start=out.after_start, end=out.after_end)
    except ValueError as e:
        raise Unreadable(f"the periods to compare don't make sense: {e}") from e
    return out.metric, before, after
