"""Errors in one shape for every client: RFC 9457 problem details (application/problem+json).

Each error carries a stable `code` that clients can branch on; the codes are listed in docs/errors.md, which the
`type` URL points at. `detail` is for people and may change wording at any time.
"""

import logging
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.llm import AllProvidersFailed

log = logging.getLogger(__name__)

DOCS = "https://github.com/Arnavsharma2005/nl-analytics-agent/blob/main/docs/errors.md"


class Problem(Exception):
    """An error to report to the client. `retry_after` (seconds) is sent as a Retry-After header and in the body."""

    def __init__(self, status: int, code: str, title: str, detail: str | None = None, *,
                 retry_after: int | None = None, **extra):
        super().__init__(detail or title)
        self.status, self.code, self.title, self.detail, self.extra = status, code, title, detail, extra
        self.retry_after = retry_after

    def body(self) -> dict:
        body = {"type": f"{DOCS}#{self.code.replace('_', '-')}", "code": self.code, "title": self.title,
                "status": self.status}
        if self.detail:
            body["detail"] = self.detail
        if self.retry_after is not None:
            body["retry_after"] = self.retry_after
        return body | self.extra

    def response(self) -> JSONResponse:
        headers = {"Retry-After": str(self.retry_after)} if self.retry_after is not None else None
        return JSONResponse(self.body(), status_code=self.status, media_type="application/problem+json",
                            headers=headers)


def from_exception(exc: Exception) -> Problem:
    """The problem to report for an exception raised while answering. Unexpected errors are logged, and their
    message is not shown, since it can carry internal details."""
    if isinstance(exc, Problem):
        return exc
    if isinstance(exc, AllProvidersFailed) and exc.daily_budget_used:
        midnight = 86400 - int(datetime.now(UTC).timestamp()) % 86400
        return Problem(503, "daily_limit_reached", "Today's free model budget is used up",
                       "Every model provider has used its free calls for today. Saved answers still work; "
                       "new questions can be asked again after midnight UTC.", retry_after=midnight)
    if isinstance(exc, AllProvidersFailed):
        return Problem(503, "all_providers_failed", "No model provider is available right now", str(exc),
                       retry_after=60, errors=exc.errors)
    log.exception("unexpected error while answering", exc_info=exc)
    return Problem(500, "internal_error", "Something went wrong on our side")


def install(app: FastAPI) -> None:
    @app.exception_handler(Problem)
    @app.exception_handler(AllProvidersFailed)
    @app.exception_handler(Exception)
    def answer(_: Request, exc: Exception):
        return from_exception(exc).response()

    @app.exception_handler(RequestValidationError)
    def invalid(_: Request, exc: RequestValidationError):
        errors = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
        return Problem(422, "invalid_request", "The request is not valid", errors=errors).response()

    @app.exception_handler(HTTPException)
    def http(_: Request, exc: HTTPException):
        code = {404: "not_found", 405: "method_not_allowed", 503: "unavailable"}.get(exc.status_code, "http_error")
        return Problem(exc.status_code, code, str(exc.detail)).response()
