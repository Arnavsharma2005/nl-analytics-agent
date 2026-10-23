"""/v1 routes for Deep Analysis reports (app/deep_analysis.py, stored by app/reports.py).

A report runs many analyst answers, so it is for signed-in users only, one at a time, and a few a day. Starting one
returns at once; the report runs in the background and its page polls GET /v1/reports/{id} until it is done.
"""

import logging
from collections.abc import Callable
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Request
from pydantic import BaseModel, Field

from app.deep_analysis import DeepAnalysis
from app.problems import Problem
from app.reports import Reports, ReportStatusV1
from app.usage import DAY, Counters, Visitor

log = logging.getLogger(__name__)

NOT_FOUND = ("report_not_found", "No such report", "Reports are kept for 30 days, and only their owner can open them.")


class ReportRequestV1(BaseModel):
    question: str = Field(min_length=3, max_length=500,
                          description='A broad question, e.g. "How did Q4 go and where should we focus?"')


def reports_router(*, reports: Callable[[], Reports], analysis: Callable[[], DeepAnalysis],
                   counters: Callable[[], Counters], identify: Callable[[Request], Visitor], per_day: int,
                   enabled: Callable[[int | None], bool] = lambda _: True) -> APIRouter:
    """`enabled` says whether Deep Analysis is on for a user (app/flags.py)."""
    router = APIRouter(prefix="/v1", tags=["v1"])

    def owner_of(request: Request) -> str:
        who = identify(request)
        if not enabled(who.user):
            raise Problem(404, "reports_off", "Deep Analysis is not turned on here",
                          "Set DEEP_ANALYSIS=true on the server, or switch its flag on, to offer reports.")
        if not who.user:
            raise Problem(401, "sign_in_required", "Sign in to use Deep Analysis",
                          "Reports run many queries, so they are for signed-in users.")
        return who.owner

    def run(id: UUID, question: str) -> None:
        store = reports()
        try:
            report = analysis().run(question, lambda stage, done, total: store.progress(id, stage, done, total))
        except Exception as e:  # noqa: BLE001 - the report is marked failed, and the reason shown to its owner
            log.exception("deep analysis failed")
            store.fail(id, f"The report could not be finished: {e}")
            return
        store.finish(id, report)

    @router.post("/reports", status_code=202)
    def start(body: ReportRequestV1, request: Request, background: BackgroundTasks) -> ReportStatusV1:
        """Start a report on a broad question. It runs for a minute or two: poll GET /v1/reports/{id}. One report at
        a time per user, and a few a day."""
        owner = owner_of(request)
        store = reports()
        if store.running(owner):
            raise Problem(409, "report_running", "A report is already running",
                          "Wait for it to finish before starting another.")
        if per_day:
            spent = counters().spend(f"reports:{owner}", DAY, per_day)
            if not spent.allowed:
                raise Problem(429, "report_quota_exceeded", f"You can run {per_day} reports a day",
                              "Please try again tomorrow.", retry_after=spent.retry_after_s)
        started = store.start(owner, body.question.strip())
        background.add_task(run, started.id, started.question)
        return started

    @router.get("/reports")
    def list_reports(request: Request) -> list[ReportStatusV1]:
        """Your latest reports, newest first, without their bodies."""
        return reports().list(owner_of(request))

    @router.get("/reports/{report_id}")
    def get_report(report_id: UUID, request: Request) -> ReportStatusV1:
        """A report: its progress while it runs, and the report once it is done."""
        found = reports().get(report_id, owner_of(request))
        if found is None:
            raise Problem(404, *NOT_FOUND)
        return found

    return router
