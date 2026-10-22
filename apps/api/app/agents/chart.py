import json
import re
from typing import Literal

from pydantic import BaseModel, Field

from app.llm import LLM

ChartType = Literal["bar", "line", "pie", "scatter", "kpi", "table"]
ColumnKind = Literal["numeric", "temporal", "categorical"]

SYSTEM = """You choose the best visualization for a query result. Rules of thumb:
- line: a measure over time (x is a date or period column). Use `series` for one line per category
  when the result is in long format (date, category, value).
- bar: compare a measure across categories, or rankings (top N). Use `series` for grouped bars.
- pie: share of a whole across at most 8 categories, one measure, and only when the question is about
  share, split, mix or breakdown.
- scatter: relationship between two numeric measures.
- kpi: a single row holding one to three headline numbers.
- table: anything else, e.g. lists of records with many text columns.
x, y and series must be column names from the result. y lists the numeric measure column(s)."""


class ChartSpec(BaseModel):
    type: ChartType
    x: str | None = Field(default=None, description="Column for the x axis or pie labels; null for kpi/table.")
    y: list[str] = Field(default_factory=list, description="Numeric measure column(s).")
    series: str | None = Field(default=None, description="Optional categorical column that splits lines/bars.")
    title: str = Field(description="Short chart title in plain English.")


MAX_SERIES = 8

_DATE = re.compile(r"^\d{4}-\d{2}(-\d{2})?([T ][\d:.]+)?$")


def column_kinds(columns: list[str], rows: list[list]) -> dict[str, ColumnKind]:
    kinds: dict[str, ColumnKind] = {}
    for i, col in enumerate(columns):
        values = [r[i] for r in rows if r[i] is not None]
        if values and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
            kinds[col] = "numeric"
        elif values and all(isinstance(v, str) and _DATE.match(v) for v in values):
            kinds[col] = "temporal"
        elif not values and re.search(r"(date|month|week|day|quarter|year)", col):
            kinds[col] = "temporal"
        else:
            kinds[col] = "categorical"
    # Year columns come back as numbers but read better as labels on an axis.
    for col in columns:
        if kinds[col] == "numeric" and re.fullmatch(r"(year|yr|order_year|signup_year)", col):
            kinds[col] = "categorical"
    return kinds


def _distinct(columns: list[str], rows: list[list], col: str) -> int:
    i = columns.index(col)
    return len({r[i] for r in rows})


def validate_chart(spec: ChartSpec, columns: list[str], rows: list[list]) -> str | None:
    """Return a problem description, or None if the spec can be drawn from this result."""
    kinds = column_kinds(columns, rows)
    if spec.type == "table":
        return None
    for col in [spec.x, spec.series, *spec.y]:
        if col is not None and col not in kinds:
            return f"column {col!r} is not in the result"
    if not spec.y or any(kinds[c] != "numeric" for c in spec.y):
        return "y must list numeric columns"
    if spec.type == "kpi":
        return None if len(rows) == 1 else "kpi needs exactly one row"
    if spec.x is None:
        return f"{spec.type} needs an x column"
    if spec.series and (len(spec.y) != 1 or kinds[spec.series] != "categorical" or spec.series == spec.x):
        return "series must be a different categorical column, with a single y"
    if spec.series and _distinct(columns, rows, spec.series) > MAX_SERIES:
        return f"series has more than {MAX_SERIES} values"
    if spec.type == "line":
        return None if len(rows) >= 2 and kinds[spec.x] in ("temporal", "numeric", "categorical") else "line needs 2+ rows"
    if spec.type == "bar":
        return None if len(rows) <= 60 else "too many bars"
    if spec.type == "pie":
        if len(spec.y) != 1 or spec.series:
            return "pie takes one measure"
        if not 2 <= len(rows) <= 8:
            return "pie needs 2-8 slices"
        y = columns.index(spec.y[0])
        return None if all((r[y] or 0) >= 0 for r in rows) else "pie values must be non-negative"
    if spec.type == "scatter":
        return None if kinds[spec.x] == "numeric" and len(spec.y) == 1 else "scatter needs numeric x and one y"
    return "unknown chart type"


def heuristic_chart(question: str, columns: list[str], rows: list[list]) -> ChartSpec:
    """Rule-based fallback used when the model's choice cannot be drawn."""
    title = question.strip().rstrip("?")[:80]
    kinds = column_kinds(columns, rows)
    numeric = [c for c in columns if kinds[c] == "numeric" and not c.endswith("_id")]
    temporal = [c for c in columns if kinds[c] == "temporal"]
    categorical = [c for c in columns if kinds[c] == "categorical"]
    if not rows or not numeric:
        return ChartSpec(type="table", title=title)
    if len(rows) == 1 and len(numeric) <= 3:
        return ChartSpec(type="kpi", y=numeric, title=title)
    if temporal:
        series = categorical[0] if len(categorical) == 1 and len(numeric) == 1 else None
        if series and _distinct(columns, rows, series) > MAX_SERIES:
            return ChartSpec(type="table", title=title)
        return ChartSpec(type="line", x=temporal[0], y=numeric[:1] if series else numeric[:3], series=series, title=title)
    if categorical:
        share = re.search(r"\b(share|split|mix|breakdown|proportion|distribution)\b", question, re.I)
        if share and len(numeric) == 1 and 2 <= len(rows) <= 8:
            return ChartSpec(type="pie", x=categorical[0], y=numeric, title=title)
        if len(categorical) == 2 and len(numeric) == 1:
            if _distinct(columns, rows, categorical[1]) <= MAX_SERIES:
                return ChartSpec(type="bar", x=categorical[0], y=numeric, series=categorical[1], title=title)
            return ChartSpec(type="table", title=title)
        if len(rows) <= 60:
            return ChartSpec(type="bar", x=categorical[0], y=numeric[:3], title=title)
    if len(numeric) >= 2 and not categorical:
        return ChartSpec(type="scatter", x=numeric[0], y=[numeric[1]], title=title)
    return ChartSpec(type="table", title=title)


def choose_chart(llm: LLM, question: str, columns: list[str], rows: list[list], sample: int) -> tuple[ChartSpec, str]:
    """Ask the model for a chart; fall back to rules if its spec does not fit the data.

    Returns the spec and where it came from ("llm" or "fallback")."""
    if not rows:
        return ChartSpec(type="table", title=question.strip().rstrip("?")[:80]), "fallback"
    kinds = column_kinds(columns, rows)
    user = (
        f"Question: {question}\n"
        f"Result: {len(rows)} rows. Columns: " + ", ".join(f"{c} ({kinds[c]})" for c in columns) + "\n"
        f"First rows: {json.dumps(rows[:sample], default=str)}"
    )
    try:
        spec = llm.structured(ChartSpec, SYSTEM, user, tier="fast")
    except Exception:  # noqa: BLE001 - a chart is never worth failing the answer for
        return heuristic_chart(question, columns, rows), "fallback"
    if validate_chart(spec, columns, rows) is None:
        return spec, "llm"
    return heuristic_chart(question, columns, rows), "fallback"
