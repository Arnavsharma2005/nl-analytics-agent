"""Driver analysis: why a metric changed between two periods, split by each dimension, computed in code.

The data owner describes the metrics and dimensions once, in semantic/drivers.yml (the base tables and joins, the
date column, how each metric is computed, and the columns to split by). For a metric and two periods, one query
with GROUPING SETS returns the metric for both periods in total and for every group of every dimension; the change
is then split exactly:

- an additive metric (a sum or a count): each group's change, and its share of the total change;
- a ratio metric (average order value = revenue / orders): each group's own ratio before and after, and its
  contribution to the change, split into a mix effect (its share of the denominator moved) and a rate effect (its
  own ratio moved). Within each dimension the contributions add up to the change.

A dimension whose groups don't add up to the total (orders split by product category, where one order holds several
categories) can't split that metric, and is left out. The model only picks the metric and periods and narrates the
table; every figure comes from the query.
"""

from datetime import date
from functools import cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_DRIVERS = 24  # groups shown, biggest movers first


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Metric(_Strict):
    id: str
    label: str
    sum: str | None = None  # SQL expression summed over the base rows
    count_distinct: str | None = None  # SQL expression whose distinct values are counted
    ratio: tuple[str, str] | None = None  # ids of two additive metrics: numerator, denominator
    description: str = ""

    @model_validator(mode="after")
    def _one_kind(self) -> "Metric":
        if sum(x is not None for x in (self.sum, self.count_distinct, self.ratio)) != 1:
            raise ValueError(f"metric {self.id}: give exactly one of sum, count_distinct or ratio")
        return self

    @property
    def kind(self) -> Literal["additive", "ratio"]:
        return "ratio" if self.ratio else "additive"


class Dimension(_Strict):
    id: str
    label: str
    sql: str


class Drivers(_Strict):
    """semantic/drivers.yml: what driver analysis can split, and by what."""

    tables: str = Field(description="The FROM clause: base tables and their joins.")
    where: str | None = Field(default=None, description="A condition every row must meet, e.g. completed orders.")
    date: str = Field(description="The date column that places a row in a period.")
    metrics: tuple[Metric, ...]
    dimensions: tuple[Dimension, ...]

    @model_validator(mode="after")
    def _ratios_name_additive_metrics(self) -> "Drivers":
        additive = {m.id for m in self.metrics if m.kind == "additive"}
        for m in self.metrics:
            if m.ratio and not set(m.ratio) <= additive:
                raise ValueError(f"metric {m.id}: a ratio is of two sum or count_distinct metrics")
        if len({m.id for m in self.metrics}) != len(self.metrics) or len({d.id for d in self.dimensions}) != len(
                self.dimensions):
            raise ValueError("metric and dimension ids must be unique")
        return self

    def metric(self, metric_id: str) -> Metric:
        return next(m for m in self.metrics if m.id == metric_id)


@cache
def load(directory: Path | None = None) -> Drivers | None:
    """semantic/drivers.yml in `directory` (by default SEMANTIC_DIR if set, else the demo's); None without one."""
    if directory is None:
        from app import semantic
        from app.config import get_settings

        directory = get_settings().semantic_dir or semantic.SEMANTIC_DIR
    file = directory / "drivers.yml"
    if not file.exists():
        return None
    return Drivers.model_validate(yaml.safe_load(file.read_text(encoding="utf-8")))


class Period(_Strict):
    start: date
    end: date  # inclusive

    @model_validator(mode="after")
    def _ordered(self) -> "Period":
        if self.end < self.start:
            raise ValueError("a period ends on or after its start")
        return self

    def describe(self) -> str:
        return f"{self.start:%d %b %Y} to {self.end:%d %b %Y}"


def _measures(drivers: Drivers, metric: Metric, before: Period, after: Period) -> list[tuple[str, str]]:
    """(alias, SQL) for each additive part of the metric, in each period."""
    parts = [drivers.metric(i) for i in metric.ratio] if metric.ratio else [metric]
    out = []
    for name, period in (("before", before), ("after", after)):
        when = f"{drivers.date} BETWEEN DATE '{period.start.isoformat()}' AND DATE '{period.end.isoformat()}'"
        for n, part in enumerate(parts):
            sql = (f"SUM(CASE WHEN {when} THEN {part.sum} ELSE 0 END)" if part.sum
                   else f"COUNT(DISTINCT CASE WHEN {when} THEN {part.count_distinct} END)")
            out.append((f"{name}_{n}", sql))
    return out


def query(drivers: Drivers, metric: Metric, before: Period, after: Period) -> str:
    """One query: the metric's parts in both periods, in total and by every group of every dimension."""
    dims = drivers.dimensions
    which = " ".join(f"WHEN GROUPING({d.sql}) = 0 THEN '{d.id}'" for d in dims)
    group = " ".join(f"WHEN GROUPING({d.sql}) = 0 THEN CAST({d.sql} AS TEXT)" for d in dims)
    measures = ",\n  ".join(f"{sql} AS {alias}" for alias, sql in _measures(drivers, metric, before, after))
    in_periods = " OR ".join(
        f"{drivers.date} BETWEEN DATE '{p.start.isoformat()}' AND DATE '{p.end.isoformat()}'" for p in (before, after))
    where = f"({drivers.where}) AND ({in_periods})" if drivers.where else in_periods
    sets = ", ".join(f"({d.sql})" for d in dims) + ", ()"
    return (f"SELECT CASE {which} ELSE 'total' END AS dimension,\n"
            f"  CASE {group} END AS grp,\n  {measures}\n"
            f"FROM {drivers.tables}\nWHERE {where}\nGROUP BY GROUPING SETS ({sets})")


class Split(BaseModel):
    """The change split by every dimension that can split it, ready to show and narrate."""

    columns: list[str]
    rows: list[list[Any]]
    truncated: bool
    left_out: list[str] = Field(description="Dimensions whose groups don't add up to the total for this metric.")


def _number(v: Any) -> float:
    return float(v or 0)


def _adds_up(total: list[float], groups: list[list[float]]) -> bool:
    return all(abs(sum(g[i] for g in groups) - t) <= 1e-6 * max(abs(t), 1.0) for i, t in enumerate(total))


def split(drivers: Drivers, metric: Metric, columns: list[str], rows: list[list[Any]]) -> Split:
    """The query's rows as drivers: each group's change (and, for a ratio, its mix and rate effects), the biggest
    movers first, after the total."""
    at = {name: i for i, name in enumerate(columns)}
    parts = 2 if metric.ratio else 1
    values = [f"{p}_{n}" for p in ("before", "after") for n in range(parts)]

    def measures(row: list[Any]) -> list[float]:
        return [_number(row[at[v]]) for v in values]

    total = next((measures(r) for r in rows if r[at["dimension"]] == "total"), [0.0] * len(values))
    by_dimension: dict[str, list[tuple[str, list[float]]]] = {}
    for r in rows:
        if r[at["dimension"]] != "total":
            by_dimension.setdefault(r[at["dimension"]], []).append((str(r[at["grp"]]), measures(r)))

    labels = {d.id: d.label for d in drivers.dimensions}
    usable = {d: g for d, g in by_dimension.items() if _adds_up(total, [m for _, m in g])}
    left_out = [labels[d] for d in by_dimension if d not in usable]

    if metric.kind == "additive":
        before, after = total
        change = after - before
        out = [[f"{labels[d]}: {name}", m[0], m[1], m[1] - m[0],
                round(100 * (m[1] - m[0]) / change, 1) if change else None]
               for d, groups in usable.items() for name, m in groups]
        head = [["Total", before, after, change, 100.0 if change else None]]
        cols = ["driver", "before", "after", "change", "share_of_change_pct"]
    else:
        nb, db, na, da = total
        before = nb / db if db else 0.0
        after = na / da if da else 0.0
        out = []
        for d, groups in usable.items():
            for name, (gnb, gdb, gna, gda) in groups:
                wb, wa = (gdb / db if db else 0.0), (gda / da if da else 0.0)
                rb, ra = (gnb / gdb if gdb else None), (gna / gda if gda else None)
                base = rb if rb is not None else ra or 0.0  # a group new in the later period mixes in at its rate
                mix = (wa - wb) * base
                rate = wa * (ra - rb) if ra is not None and rb is not None else 0.0
                out.append([f"{labels[d]}: {name}", rb, ra, mix + rate, mix, rate])
        head = [["Total", before, after, after - before, None, None]]
        cols = ["driver", "before", "after", "contribution", "mix_effect", "rate_effect"]

    out.sort(key=lambda r: -abs(r[3]))
    shown = [_rounded(r) for r in head + out[:MAX_DRIVERS]]
    return Split(columns=cols, rows=shown, truncated=len(out) > MAX_DRIVERS, left_out=left_out)


def _rounded(row: list[Any]) -> list[Any]:
    return [round(v, 2) if isinstance(v, float) else v for v in row]
