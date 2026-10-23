"""Deep Analysis reports as stored: started by POST /v1/reports, written as they run, read by their owner.

A report runs in the background for a minute or two, updating its row after each step, so the page can show
progress by polling. A report still marked running long after its last update was cut off (the server restarted
mid-run) and reads as failed. Reports are kept for KEEP_DAYS.
"""

import json
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.deep_analysis import ReportV1

KEEP_DAYS = 30
STALE = timedelta(minutes=10)  # no step takes this long; a running report silent for longer was cut off
INTERRUPTED = "The report was interrupted. Please try again."

Stage = Literal["planning", "planned", "step", "writing", "done"]


class ReportStatusV1(BaseModel):
    """A report: how far it has got, and the report itself once it is done."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    id: UUID
    question: str
    status: Literal["running", "done", "failed"]
    stage: Stage
    steps_done: int
    steps_total: int | None = Field(description="How many steps the plan has; none until it is planned.")
    report: ReportV1 | None
    error: str | None
    created_at: datetime
    updated_at: datetime


class Reports:
    def __init__(self, engine: Engine):
        self._engine = engine

    def start(self, owner: str, question: str) -> ReportStatusV1:
        with self._engine.begin() as conn:
            conn.execute(text("DELETE FROM app.reports WHERE created_at < now() - :keep"),
                         {"keep": timedelta(days=KEEP_DAYS)})
            row = conn.execute(text("""
                INSERT INTO app.reports (id, owner, question, status, stage) VALUES (:id, :owner, :q, 'running', 'planning')
                RETURNING *
            """), {"id": uuid4(), "owner": owner, "q": question}).one()
        return self._status(row)

    def progress(self, id: UUID, stage: Stage, done: int, total: int) -> None:
        with self._engine.begin() as conn:
            conn.execute(text("""
                UPDATE app.reports SET stage = :stage, steps_done = :done, steps_total = :total, updated_at = now()
                WHERE id = :id AND status = 'running'
            """), {"id": id, "stage": stage, "done": done, "total": total})

    def finish(self, id: UUID, report: ReportV1) -> None:
        with self._engine.begin() as conn:
            conn.execute(text("""
                UPDATE app.reports SET status = 'done', stage = 'done', report = CAST(:report AS jsonb),
                                       updated_at = now()
                WHERE id = :id
            """), {"id": id, "report": json.dumps(report.model_dump(mode="json"))})

    def fail(self, id: UUID, error: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(text("UPDATE app.reports SET status = 'failed', error = :error, updated_at = now() "
                              "WHERE id = :id"), {"id": id, "error": error})

    def get(self, id: UUID, owner: str) -> ReportStatusV1 | None:
        with self._engine.connect() as conn:
            row = conn.execute(text("SELECT * FROM app.reports WHERE id = :id AND owner = :owner"),
                               {"id": id, "owner": owner}).first()
        return self._status(row) if row else None

    def list(self, owner: str, limit: int = 20) -> list[ReportStatusV1]:
        """The owner's latest reports, newest first, without their bodies (open one to read it)."""
        with self._engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT id, owner, question, status, stage, steps_done, steps_total, NULL AS report, error, created_at,
                       updated_at
                FROM app.reports WHERE owner = :owner ORDER BY created_at DESC LIMIT :limit
            """), {"owner": owner, "limit": limit}).all()
        return [self._status(r) for r in rows]

    def running(self, owner: str) -> int:
        with self._engine.connect() as conn:
            return conn.execute(text("""
                SELECT count(*) FROM app.reports
                WHERE owner = :owner AND status = 'running' AND updated_at > now() - :stale
            """), {"owner": owner, "stale": STALE}).scalar_one()

    @staticmethod
    def _status(row) -> ReportStatusV1:
        status, error = row.status, row.error
        if status == "running" and row.updated_at < datetime.now(row.updated_at.tzinfo) - STALE:
            status, error = "failed", INTERRUPTED
        return ReportStatusV1(id=row.id, question=row.question, status=status, stage=row.stage,
                              steps_done=row.steps_done, steps_total=row.steps_total,
                              report=ReportV1.model_validate(row.report) if row.report else None, error=error,
                              created_at=row.created_at, updated_at=row.updated_at)
