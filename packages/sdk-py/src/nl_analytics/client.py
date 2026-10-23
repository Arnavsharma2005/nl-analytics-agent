"""A client for the analytics agent's /v1 API: ask questions about the data, read the weekly insights, and run
Deep Analysis reports.

    from nl_analytics import Analyst

    analyst = Analyst("https://nl-analytics-agent.onrender.com", api_key="aak_...")
    message = analyst.ask("Revenue by region last quarter")
    print(message.answer.summary, message.answer.rows)

    chat = analyst.conversation()          # follow-ups are understood with the earlier questions
    chat.ask("Revenue by region")
    chat.ask("and last year?")

Make a key at /keys on the web app (signed in). Every request with it asks as you, within your question limits.
"""

import time
from types import TracebackType
from typing import Any, Self
from uuid import UUID, uuid4

import httpx

from nl_analytics.models import InsightsV1, MessageV1, ReportStatusV1

USER_AGENT = "nl-analytics-sdk-py/0.1.0"


class ApiError(Exception):
    """The API refused or failed a request. `code` is stable (e.g. "quota_exceeded", "invalid_api_key");
    `retry_after` is how many seconds to wait, when the API says."""

    def __init__(self, status: int, code: str, title: str, detail: str | None = None,
                 retry_after: int | None = None):
        super().__init__(f"{status} {code}: {title}" + (f" ({detail})" if detail else ""))
        self.status, self.code, self.title, self.detail, self.retry_after = status, code, title, detail, retry_after


class Analyst:
    """`base_url` is the app's address. `http` replaces the HTTP client (for proxies, retries or tests); the
    key is still sent on every request."""

    def __init__(self, base_url: str, api_key: str | None = None, *, timeout: float = 120.0,
                 http: httpx.Client | None = None):
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._own = http is None
        self._http = http or httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)
        self._headers = headers

    def __enter__(self) -> Self:
        return self

    def __exit__(self, kind: type[BaseException] | None, error: BaseException | None, tb: TracebackType | None):
        self.close()

    def close(self) -> None:
        if self._own:
            self._http.close()

    def _request(self, method: str, path: str, **kw: Any) -> Any:
        r = self._http.request(method, path, headers={**self._headers, **kw.pop("headers", {})}, **kw)
        if r.is_success:
            return r.json() if r.content else None
        try:
            body = r.json()
        except ValueError:
            body = {}
        retry = r.headers.get("retry-after")
        raise ApiError(r.status_code, body.get("code", "http_error"), body.get("title", r.reason_phrase),
                       body.get("detail"), int(retry) if retry and retry.isdigit() else None)

    def ask(self, question: str, *, conversation_id: UUID | None = None, dataset_id: UUID | None = None,
            idempotency_key: UUID | None = None) -> MessageV1:
        """Ask one question and wait for the answer. Pass the same `conversation_id` for follow-ups. Sending the same
        `idempotency_key` with the same conversation again returns the first answer instead of asking twice, so a retry
        after a dropped connection is safe."""
        body: dict[str, Any] = {"question": question}
        if dataset_id:
            body["dataset_id"] = str(dataset_id)
        data = self._request("POST", f"/v1/conversations/{conversation_id or uuid4()}/messages", json=body,
                             headers={"Idempotency-Key": str(idempotency_key or uuid4())})
        return MessageV1.model_validate(data)

    def conversation(self, conversation_id: UUID | None = None) -> "Conversation":
        """A conversation, so follow-up questions are understood with the earlier ones."""
        return Conversation(self, conversation_id or uuid4())

    def insights(self) -> InsightsV1:
        """The latest week's most notable moves in the data, found nightly."""
        return InsightsV1.model_validate(self._request("GET", "/v1/insights"))

    def start_report(self, question: str) -> ReportStatusV1:
        """Start a Deep Analysis report on a broad question (signed-in users only, a few a day)."""
        return ReportStatusV1.model_validate(self._request("POST", "/v1/reports", json={"question": question}))

    def report(self, report_id: UUID) -> ReportStatusV1:
        return ReportStatusV1.model_validate(self._request("GET", f"/v1/reports/{report_id}"))

    def wait_for_report(self, report_id: UUID, *, every: float = 3.0, timeout: float = 600.0) -> ReportStatusV1:
        """Poll a report until it is done or failed; raises TimeoutError after `timeout` seconds."""
        deadline = time.monotonic() + timeout
        while True:
            status = self.report(report_id)
            if status.status != "running":
                return status
            if time.monotonic() > deadline:
                raise TimeoutError(f"report {report_id} is still running after {timeout:.0f}s")
            time.sleep(every)


class Conversation:
    def __init__(self, analyst: Analyst, id: UUID):
        self._analyst, self.id = analyst, id

    def ask(self, question: str, *, dataset_id: UUID | None = None) -> MessageV1:
        return self._analyst.ask(question, conversation_id=self.id, dataset_id=dataset_id)
