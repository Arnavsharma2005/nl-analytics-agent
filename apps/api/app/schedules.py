"""Scheduled reports and alerts: a question asked daily or weekly, its answer sent by email or Telegram.

A report sends the answer every time. An alert asks a question whose answer is one number ("revenue yesterday"),
and sends only when that number crosses its threshold: once when it starts meeting the condition, not again until it
has stopped meeting it and meets it again.

Schedules belong to signed-in users (POST /v1/schedules, app/api_schedules.py). The job app/jobs/schedules.py runs
the due ones every hour. Each due schedule is claimed by moving its next run forward before it is asked, so two runs
of the job never send the same thing twice, and a failure waits for the next run instead of retrying in a loop.

Email goes through Resend (RESEND_API_KEY, EMAIL_FROM) and only to the addresses or domains in
SCHEDULE_EMAIL_ALLOWLIST, so a schedule cannot be used to send mail to strangers. Telegram goes to a chat that has
started the bot (its id comes from the bot's /id command).
"""

import html
import logging
import operator
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.analyst import Analyst, Answer
from app.telegram import TelegramClient, reply_text, table_text

log = logging.getLogger(__name__)

PER_USER = 10
RESEND = "https://api.resend.com/emails"
OPS: dict[str, Callable[[float, float], bool]] = {">": operator.gt, ">=": operator.ge, "<": operator.lt,
                                                    "<=": operator.le}
Channel = Literal["email", "telegram"]


class AlertV1(BaseModel):
    op: Literal[">", ">=", "<", "<="]
    value: float = Field(description="Send when the answer's number compared with this holds.")


class ScheduleRequestV1(BaseModel):
    question: str = Field(min_length=3, max_length=500,
                          description='For an alert, a question with one number as its answer, e.g. "Revenue '
                                      'yesterday".')
    kind: Literal["report", "alert"] = "report"
    every: Literal["day", "week"] = "day"
    hour_utc: int = Field(default=3, ge=0, le=23, description="When to run, as an hour of the day in UTC.")
    weekday: int = Field(default=0, ge=0, le=6, description="For weekly schedules: 0 is Monday.")
    channel: Channel
    to: str = Field(min_length=1, max_length=254, description="An email address, or a Telegram chat id (the bot's "
                                                              "/id command shows it).")
    alert: AlertV1 | None = Field(default=None, description="Required for an alert, absent for a report.")

    @model_validator(mode="after")
    def _check(self) -> "ScheduleRequestV1":
        if (self.kind == "alert") != (self.alert is not None):
            raise ValueError("an alert needs `alert` (op and value), and a report must not have one")
        self.to = self.to.strip()
        if self.channel == "email" and email_domain(self.to) is None:
            raise ValueError("`to` must be an email address")
        if self.channel == "telegram" and not re.fullmatch(r"-?\d{1,20}", self.to):
            raise ValueError("`to` must be a Telegram chat id, a number")
        return self


class ScheduleV1(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    id: UUID
    question: str
    kind: Literal["report", "alert"]
    every: Literal["day", "week"]
    hour_utc: int
    weekday: int
    channel: Channel
    to: str
    alert: AlertV1 | None
    next_run_at: datetime
    last_run_at: datetime | None
    last_status: str | None = Field(description='"sent", "quiet" (an alert that did not fire) or what went wrong.')
    last_value: float | None = Field(description="An alert's number at its last run.")
    created_at: datetime


def next_run(every: str, hour: int, weekday: int, after: datetime) -> datetime:
    """The first run time strictly after `after`."""
    at = after.astimezone(UTC).replace(minute=0, second=0, microsecond=0, hour=hour)
    step = timedelta(days=1)
    while at <= after or (every == "week" and at.weekday() != weekday):
        at += step
    return at


def first_number(answer: Answer) -> float | None:
    """The number an alert compares: the first numeric cell of the first row."""
    for value in (answer.rows[0] if answer.rows else []):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def email_domain(address: str) -> str | None:
    """The domain of a plain address like name@example.com, or None. (No regular expression: addresses are input.)"""
    local, at, domain = address.partition("@")
    if not at or not local or "@" in domain or any(c.isspace() for c in address):
        return None
    labels = domain.split(".")
    return domain.lower() if len(labels) > 1 and all(labels) else None


def allowed_email(address: str, allowlist: list[str]) -> bool:
    domain = email_domain(address.strip())
    if domain is None:
        return False
    return any(a.lower() in (address.strip().lower(), domain, "@" + domain) for a in allowlist)


class Notifier:
    """Sends a schedule's message. A channel without its settings is None and refused when a schedule is made."""

    def __init__(self, resend_key: str | None, email_from: str | None, telegram: TelegramClient | None,
                 post: Callable[..., httpx.Response] | None = None):
        self._key, self._from, self.telegram = resend_key, email_from, telegram
        self._post = post or httpx.post

    @property
    def email(self) -> bool:
        return bool(self._key and self._from)

    def send_email(self, to: str, subject: str, body: str) -> None:
        r = self._post(RESEND, headers={"Authorization": f"Bearer {self._key}"}, timeout=20,
                       json={"from": self._from, "to": [to], "subject": subject[:200], "html": body})
        r.raise_for_status()

    def send_telegram(self, chat_id: int, body: str) -> None:
        if not self.telegram:
            raise RuntimeError("the Telegram bot is not set up")
        if not self.telegram.send_message(chat_id, body):
            raise RuntimeError("Telegram did not take the message (has the chat started the bot?)")


def email_body(question: str, answer: Answer, headline: str | None, app_url: str | None) -> str:
    parts = [f"<p><strong>{html.escape(headline)}</strong></p>"] if headline else []
    if answer.error or answer.intent != "data_query":
        parts.append(f"<p>{html.escape(answer.answer or 'This question could not be answered this time.')}</p>")
    else:
        parts.append(f"<p>{html.escape(answer.summary or '')}</p>")
        if answer.columns and answer.rows:
            parts.append(f"<pre style=\"font-size:13px\">{html.escape(table_text(answer.columns, answer.rows))}</pre>")
    footer = f"Scheduled question: {html.escape(question)}"
    if app_url:
        footer += f' · <a href="{html.escape(app_url)}">Open the app</a> to change or stop it.'
    parts.append(f'<p style="color:#666;font-size:12px">{footer}</p>')
    return "\n".join(parts)


class Schedules:
    def __init__(self, engine: Engine, clock: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self._engine, self._clock = engine, clock

    def create(self, user_id: int, request: ScheduleRequestV1) -> ScheduleV1 | None:
        """The new schedule, or None when the user already has PER_USER."""
        first = next_run(request.every, request.hour_utc, request.weekday, self._clock())
        with self._engine.begin() as conn:
            conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('schedules'), :user)"), {"user": user_id})
            if conn.execute(text("SELECT count(*) FROM app.schedules WHERE user_id = :user"),
                            {"user": user_id}).scalar_one() >= PER_USER:
                return None
            row = conn.execute(text("""
                INSERT INTO app.schedules (id, user_id, kind, question, every, hour_utc, weekday, channel, recipient,
                                           op, threshold, next_run_at)
                VALUES (:id, :user, :kind, :question, :every, :hour, :weekday, :channel, :to, :op, :threshold, :next)
                RETURNING *
            """), {"id": uuid4(), "user": user_id, "kind": request.kind, "question": request.question.strip(),
                   "every": request.every, "hour": request.hour_utc, "weekday": request.weekday,
                   "channel": request.channel, "to": request.to, "op": request.alert.op if request.alert else None,
                   "threshold": request.alert.value if request.alert else None, "next": first}).one()
        return self._schedule(row)

    def list(self, user_id: int) -> list[ScheduleV1]:
        with self._engine.connect() as conn:
            rows = conn.execute(text("SELECT * FROM app.schedules WHERE user_id = :user ORDER BY created_at"),
                                {"user": user_id}).all()
        return [self._schedule(r) for r in rows]

    def delete(self, id: UUID, user_id: int) -> bool:
        with self._engine.begin() as conn:
            return conn.execute(text("DELETE FROM app.schedules WHERE id = :id AND user_id = :user"),
                                {"id": id, "user": user_id}).rowcount == 1

    def claim_due(self, limit: int = 50) -> list:
        """The schedules due now, each moved on to its next run in the same transaction, so no other run takes them."""
        now = self._clock()
        with self._engine.begin() as conn:
            rows = conn.execute(text("""
                SELECT * FROM app.schedules WHERE next_run_at <= :now ORDER BY next_run_at LIMIT :limit
                FOR UPDATE SKIP LOCKED
            """), {"now": now, "limit": limit}).all()
            for r in rows:
                conn.execute(text("UPDATE app.schedules SET next_run_at = :next WHERE id = :id"),
                             {"id": r.id, "next": next_run(r.every, r.hour_utc, r.weekday, now)})
        return rows

    def record(self, id: UUID, status: str, value: float | None, firing: bool) -> None:
        with self._engine.begin() as conn:
            conn.execute(text("""
                UPDATE app.schedules SET last_run_at = :now, last_status = :status, last_value = :value,
                                         firing = :firing
                WHERE id = :id
            """), {"id": id, "now": self._clock(), "status": status[:300], "value": value, "firing": firing})

    @staticmethod
    def _schedule(row) -> ScheduleV1:
        return ScheduleV1(id=row.id, question=row.question, kind=row.kind, every=row.every, hour_utc=row.hour_utc,
                          weekday=row.weekday, channel=row.channel, to=row.recipient,
                          alert=AlertV1(op=row.op, value=row.threshold) if row.kind == "alert" else None,
                          next_run_at=row.next_run_at, last_run_at=row.last_run_at, last_status=row.last_status,
                          last_value=row.last_value, created_at=row.created_at)


def run_due(schedules: Schedules, analyst: Analyst, notifier: Notifier, app_url: str | None = None,
            allowed: Callable[[int], bool] = lambda _: True) -> list[str]:
    """Run every due schedule once. Returns one line per schedule saying what happened. A schedule whose owner
    `allowed` refuses (its flag is not on for them) skips this run."""
    done = []
    for row in schedules.claim_due():
        if not allowed(row.user_id):
            schedules.record(row.id, "paused", row.last_value, row.firing)
            done.append(f"{row.id}: paused")
            continue
        firing, value = row.firing, None
        try:
            answer = analyst.ask(row.question)
            headline = None
            if row.kind == "alert":
                value = first_number(answer)
                if value is None:
                    schedules.record(row.id, "the answer had no number to compare", None, False)
                    done.append(f"{row.id}: no number")
                    continue
                holds = OPS[row.op](value, row.threshold)
                if not holds or row.firing:  # quiet, or still past the threshold since the last alert
                    schedules.record(row.id, "quiet", value, holds)
                    done.append(f"{row.id}: quiet ({value:g})")
                    continue
                firing = True
                headline = f"Alert: the answer is {value:,.2f}, {row.op} {row.threshold:,.2f}"
            if row.channel == "email":
                subject = headline or f"Your {'daily' if row.every == 'day' else 'weekly'} report: {row.question}"
                notifier.send_email(row.recipient, subject, email_body(row.question, answer, headline, app_url))
            else:
                body = (f"<b>{html.escape(headline)}</b>\n\n" if headline else "") + \
                       f"<i>{html.escape(row.question)}</i>\n\n" + reply_text(answer)
                notifier.send_telegram(int(row.recipient), body)
            schedules.record(row.id, "sent", value, firing)
            done.append(f"{row.id}: sent")
        except Exception as e:  # noqa: BLE001 - one schedule failing must not stop the others
            log.exception("schedule %s failed", row.id)
            schedules.record(row.id, f"failed: {type(e).__name__}: {e}", value, row.firing)
            done.append(f"{row.id}: failed")
    return done
