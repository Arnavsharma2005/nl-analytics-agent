"""The insights feed as stored: written whole by the nightly job (app/jobs/insights.py), read by GET /v1/insights.

The job finds the latest week's notable moves (app/analysis/insights.py) and replaces the feed with them, so the
API never computes anything and a failed run leaves yesterday's feed in place.
"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.analysis.insights import Finding


class InsightV1(BaseModel):
    """One notable move in the latest complete week."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    headline: str
    metric: str
    dimension: str | None = Field(description="The dimension of the group that moved; none for the metric in total.")
    group: str | None
    value: float = Field(description="The metric that week.")
    usual: float = Field(description="Its median over the weeks before.")
    z: float = Field(description="How unusual: distance from the usual week in typical spreads (robust z-score).")
    sql: str = Field(description="The query the figures came from.")
    question: str = Field(description="A question to ask next, to look into it.")


class InsightsV1(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    week_start: date | None = Field(description="Monday of the week the feed is about; none before the first run.")
    generated_at: datetime | None
    insights: list[InsightV1]


def _question(metric: str, dimension: str | None, group: str | None, week: date) -> str:
    subject = f"{metric.lower()} for {group}" if dimension else metric.lower()
    return f"Why did {subject} change in the week of {week:%d %b %Y}?"


class InsightsFeed:
    def __init__(self, engine: Engine):
        self._engine = engine

    def replace(self, week: date, findings: list[Finding], headlines: list[str]) -> None:
        with self._engine.begin() as conn:
            conn.execute(text("DELETE FROM app.insights"))
            for rank, (f, headline) in enumerate(zip(findings, headlines, strict=True), 1):
                conn.execute(text("""
                    INSERT INTO app.insights (rank, week_start, headline, metric, dimension, grp, value, usual, z, sql)
                    VALUES (:rank, :week, :headline, :metric, :dimension, :grp, :value, :usual, :z, :sql)
                """), {"rank": rank, "week": week, "headline": headline, "metric": f.metric, "dimension": f.dimension,
                       "grp": f.group, "value": f.value, "usual": f.usual, "z": f.z, "sql": f.sql})

    def latest(self) -> InsightsV1:
        with self._engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT week_start, headline, metric, dimension, grp, value, usual, z, sql, generated_at
                FROM app.insights ORDER BY rank
            """)).all()
        if not rows:
            return InsightsV1(week_start=None, generated_at=None, insights=[])
        return InsightsV1(week_start=rows[0].week_start, generated_at=rows[0].generated_at, insights=[
            InsightV1(headline=r.headline, metric=r.metric, dimension=r.dimension, group=r.grp, value=r.value,
                      usual=r.usual, z=r.z, sql=r.sql, question=_question(r.metric, r.dimension, r.grp, r.week_start))
            for r in rows])
