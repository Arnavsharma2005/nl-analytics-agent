"""The insights feed: what moved unusually in the latest complete week, found in code and put into words by a model.

For every metric in semantic/drivers.yml, one query returns its weekly value over the last few weeks, in total and
for every group of every dimension. Each series' latest complete week is compared with its own recent weeks: how
far it is from their median, in units of their typical spread (a robust z-score: median and median absolute
deviation, so one odd week in the baseline doesn't hide the next). Moves that are both unusual and large are ranked
by how unusual they are and how much of the business the group is, and the top few are kept, each with the query
behind it. A model writes each one as a headline from the figures it is given, and a plain sentence is used
when no model is available.

Run nightly by app/jobs/insights.py; the API only reads the stored results.
"""

import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from pydantic import BaseModel, Field

from app.analysis.drivers import Drivers, Metric
from app.db import DataSource
from app.llm import LLM
from app.sql_guard import validate_sql

BASELINE_WEEKS = 8
MIN_Z = 3.0  # how unusual: about 3 typical spreads from the usual week
MIN_CHANGE = 0.25  # and how large: at least 25% above or below it
TOP = 5


@dataclass(frozen=True)
class Finding:
    metric: str  # its label, e.g. "Orders"
    dimension: str | None  # e.g. "Channel"; None for the metric in total
    group: str | None  # e.g. "marketplace"
    week_start: date
    value: float
    usual: float  # the median of the baseline weeks
    z: float
    share: float  # the group's share of the metric over the baseline weeks (1 for the total)
    sql: str

    @property
    def subject(self) -> str:
        return f"{self.metric} ({self.dimension.lower()}: {self.group})" if self.dimension else self.metric

    @property
    def ratio(self) -> float | None:
        return self.value / self.usual if self.usual else None

    def describe(self) -> str:
        """The figures, as the model is given them."""
        ratio = f"{self.ratio:.2f} times" if self.ratio is not None else "up from nothing compared with"
        return (f"{self.subject}: {self.value:,.2f} in the week of {self.week_start:%d %b %Y}, {ratio} the usual "
                f"{self.usual:,.2f} over the {BASELINE_WEEKS} weeks before")

    def plain(self) -> str:
        """A headline without a model."""
        moved = "rose" if self.value > self.usual else "fell"
        times = f", {self.ratio:.1f}x the usual {self.usual:,.0f}" if self.ratio is not None else ""
        return f"{self.subject} {moved} to {self.value:,.0f} in the week of {self.week_start:%d %b}{times}."


def latest_complete_week(last: date) -> date:
    """The Monday that starts the latest week the data covers in full (weeks run Monday to Sunday)."""
    monday = last - timedelta(days=last.weekday())
    return monday if last.weekday() == 6 else monday - timedelta(days=7)


def weekly_query(drivers: Drivers, metric: Metric, first: date, last: date) -> str:
    """The metric's weekly parts from `first` to `last`, in total and by every group of every dimension."""
    parts = [drivers.metric(i) for i in metric.ratio] if metric.ratio else [metric]
    week = f"CAST(DATE_TRUNC('week', {drivers.date}) AS DATE)"
    measures = ",\n  ".join(
        (f"SUM({p.sum})" if p.sum else f"COUNT(DISTINCT {p.count_distinct})") + f" AS part_{n}"
        for n, p in enumerate(parts))
    dims = drivers.dimensions
    which = " ".join(f"WHEN GROUPING({d.sql}) = 0 THEN '{d.id}'" for d in dims)
    group = " ".join(f"WHEN GROUPING({d.sql}) = 0 THEN CAST({d.sql} AS TEXT)" for d in dims)
    span = f"{drivers.date} BETWEEN DATE '{first.isoformat()}' AND DATE '{last.isoformat()}'"
    where = f"({drivers.where}) AND {span}" if drivers.where else span
    sets = ", ".join(f"({week}, {d.sql})" for d in dims) + f", ({week})"
    return (f"SELECT {week} AS week,\n  CASE {which} ELSE 'total' END AS dimension,\n"
            f"  CASE {group} END AS grp,\n  {measures}\n"
            f"FROM {drivers.tables}\nWHERE {where}\nGROUP BY GROUPING SETS ({sets})")


def _robust_z(value: float, baseline: list[float]) -> float:
    median = statistics.median(baseline)
    mad = statistics.median(abs(v - median) for v in baseline)
    spread = max(1.4826 * mad, 0.05 * abs(median), 1e-9)  # a flat baseline still allows for 5% wobble
    return (value - median) / spread


def find(metric: Metric, labels: dict[str, str], rows: list[list[Any]], columns: list[str], weeks: list[date],
         sql: str) -> list[Finding]:
    """Unusual moves in the latest of `weeks` (oldest first) for one metric's weekly rows."""
    at = {c: i for i, c in enumerate(columns)}
    ratio = metric.ratio is not None
    series: dict[tuple[str, str | None], dict[date, list[float]]] = {}
    for r in rows:
        week = date.fromisoformat(str(r[at["week"]])[:10])
        key = (r[at["dimension"]], None if r[at["dimension"]] == "total" else str(r[at["grp"]]))
        series.setdefault(key, {})[week] = [float(r[at[f"part_{n}"]] or 0) for n in range(2 if ratio else 1)]

    def value(parts: list[float] | None) -> float | None:
        if parts is None:
            return None if ratio else 0.0  # an additive metric with no rows that week was zero
        if ratio:
            return parts[0] / parts[1] if parts[1] else None
        return parts[0]

    def size(key) -> float:
        return sum(p[-1] for w, p in series.get(key, {}).items() if w != weeks[-1])  # the denominator for ratios

    # As in driver analysis, a dimension whose groups don't add up to the total (orders by category, where one order
    # holds several) doesn't split this metric: its groups' figures would mean something else.
    overlapping = set()
    for dimension in {d for d, _ in series if d != "total"}:
        for week, parts in series.get(("total", None), {}).items():
            sums = [sum(by_week.get(week, [0.0] * len(parts))[n] for (d, _), by_week in series.items() if d == dimension)
                    for n in range(len(parts))]
            if any(abs(s - t) > 1e-6 * max(abs(t), 1.0) for s, t in zip(sums, parts, strict=True)):
                overlapping.add(dimension)
                break

    total = size(("total", None)) or 1.0
    found = []
    for key, by_week in series.items():
        if key[0] in overlapping:
            continue
        baseline = [v for w in weeks[:-1] if (v := value(by_week.get(w))) is not None]
        latest = value(by_week.get(weeks[-1]))
        if latest is None or len(baseline) < BASELINE_WEEKS // 2:
            continue
        usual = statistics.median(baseline)
        z = _robust_z(latest, baseline)
        change = abs(latest - usual) / abs(usual) if usual else float("inf")
        if abs(z) >= MIN_Z and change >= MIN_CHANGE and (usual or latest):
            dimension, group = key
            found.append(Finding(metric=metric.label, dimension=None if dimension == "total" else labels[dimension],
                                 group=group, week_start=weeks[-1], value=round(latest, 2), usual=round(usual, 2),
                                 z=round(z, 1), share=min(size(key) / total, 1.0), sql=sql))
    return found


def scan(drivers: Drivers, db: DataSource) -> tuple[date | None, list[Finding]]:
    """The latest complete week and its most notable moves, across every metric."""
    schema = db.schema()
    tables, personal = set(schema.tables), schema.personal_columns()
    where = f" WHERE {drivers.where}" if drivers.where else ""
    last = db.run(validate_sql(f"SELECT MAX({drivers.date}) AS last FROM {drivers.tables}{where}", tables, personal,
                               db.dialect)).rows[0][0]
    if last is None:
        return None, []
    week = latest_complete_week(date.fromisoformat(str(last)[:10]))
    weeks = [week - timedelta(weeks=k) for k in range(BASELINE_WEEKS, -1, -1)]
    labels = {d.id: d.label for d in drivers.dimensions}
    found: list[Finding] = []
    for metric in drivers.metrics:
        sql = validate_sql(weekly_query(drivers, metric, weeks[0], week + timedelta(days=6)), tables, personal,
                           db.dialect)
        result = db.run(sql)
        found += find(metric, labels, result.rows, result.columns, weeks, sql)
    # Unusual and big: a 4-sigma move in a group a tenth of the business ranks below a 3-sigma move in the total.
    found.sort(key=lambda f: -min(abs(f.z), 10) * (0.25 + f.share))
    return week, found[:TOP]


class Headlines(BaseModel):
    headlines: list[str] = Field(description="One headline per finding, in the same order.")


SYSTEM = """You write the headlines of a business insights feed. For each finding, write one plain sentence of at
most 20 words that says what moved, by how much, and in which week, e.g. "Marketplace orders doubled in the week
of 8 Dec, to 240 from a usual 115." Use only the figures given; round them sensibly. No advice, no speculation
about causes.{money}"""


def headlines(llm: LLM | None, findings: list[Finding], money: str | None = None) -> list[str]:
    """A headline for each finding: from the model, or plain sentences without one (or when it fails)."""
    if llm is None or not findings:
        return [f.plain() for f in findings]
    user = "\n".join(f"{n}. {f.describe()}" for n, f in enumerate(findings, 1))
    try:
        out = llm.structured(Headlines, SYSTEM.format(money=f"\n{money}" if money else ""), user, tier="fast")
    except Exception:  # noqa: BLE001 - a headline is never worth losing the feed for
        return [f.plain() for f in findings]
    if len(out.headlines) != len(findings) or not all(h.strip() for h in out.headlines):
        return [f.plain() for f in findings]
    return [h.strip() for h in out.headlines]
