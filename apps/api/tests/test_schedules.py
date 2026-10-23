"""Scheduled reports and alerts (app/schedules.py, app/api_schedules.py, app/jobs/schedules.py): made by a signed-in
user, run when due, sent by email or Telegram, and an alert sent once each time its number crosses its threshold."""

from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import text

from app.analyst import Analyst, Answer
from app.auth import Profile, Users
from app.config import get_settings
from app.main import create_app
from app.messages import Messages
from app.schedules import (
    PER_USER,
    RESEND,
    Notifier,
    ScheduleRequestV1,
    Schedules,
    allowed_email,
    first_number,
    next_run,
    run_due,
)
from app.sessions import Sessions
from app.telegram import TelegramBot, TelegramClient

from .conftest import FakeLLM

COUNT = "SELECT COUNT(*) AS orders FROM orders"


def at(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


def test_next_run():
    assert next_run("day", 3, 0, at(2026, 10, 23, 2, 59)) == at(2026, 10, 23, 3)
    assert next_run("day", 3, 0, at(2026, 10, 23, 3, 0)) == at(2026, 10, 24, 3)  # strictly after
    assert next_run("week", 3, 0, at(2026, 10, 23, 12)) == at(2026, 10, 26, 3)  # Friday to Monday
    assert next_run("week", 3, 4, at(2026, 10, 23, 2)) == at(2026, 10, 23, 3)  # later the same Friday


def test_requests_are_checked():
    ScheduleRequestV1(question="Revenue yesterday", kind="alert", channel="telegram", to="-1001",
                      alert={"op": "<", "value": 100})
    for bad in [dict(kind="alert", channel="telegram", to="1"),  # an alert needs its threshold
                dict(channel="telegram", to="1", alert={"op": ">", "value": 1}),  # a report has none
                dict(channel="email", to="not an address"),
                dict(channel="telegram", to="@someone"),
                dict(channel="telegram", to="1", hour_utc=24)]:
        with pytest.raises(ValidationError):
            ScheduleRequestV1(question="Revenue yesterday", **bad)


def test_allowed_email_and_first_number():
    allow = ["me@example.com", "team.example"]
    assert allowed_email("Me@Example.com", allow) and allowed_email("a@team.example", allow)
    assert not allowed_email("other@example.com", allow) and not allowed_email("a@evil.team.example", allow)
    assert not allowed_email("me@example.com", [])
    for bad in ("me@", "@example.com", "a@b@example.com", "a b@example.com", "me@localhost", "me@example..com"):
        assert not allowed_email(bad, ["example.com", "localhost"]), bad
    assert first_number(Answer(question="q", rows=[["North", 12, 3.5]])) == 12.0
    assert first_number(Answer(question="q", rows=[["North", True]])) is None
    assert first_number(Answer(question="q")) is None


class Outbox:
    def __init__(self):
        self.email, self.telegram, self.telegram_ok = [], [], True

    def post(self, url, json=None, timeout=None, headers=None):
        if url == RESEND:
            self.email.append(json)
            return httpx.Response(200, json={"id": "e1"}, request=httpx.Request("POST", url))
        self.telegram.append(json)
        return httpx.Response(200 if self.telegram_ok else 403, json={})


@pytest.fixture
def world(db, app_tables):
    clock = {"now": at(2026, 10, 23, 2, 0)}
    store = Schedules(app_tables, clock=lambda: clock["now"])
    out = Outbox()
    notifier = Notifier("re_test", "Sales <r@example.com>", TelegramClient("t", post=out.post), post=out.post)
    me = Users(app_tables).sign_in(Profile(github_id=1, login="arnav", name=None, avatar_url=None))
    return store, out, notifier, me, clock


def test_an_alert_fires_once_per_crossing(world, db):
    store, out, notifier, me, clock = world
    orders = db.run(COUNT).rows[0][0]
    alert = store.create(me.id, ScheduleRequestV1(question="How many orders?", kind="alert", channel="telegram",
                                                  to="42", alert={"op": ">", "value": orders - 1}))
    assert alert.next_run_at == at(2026, 10, 23, 3)
    llm = FakeLLM(sql=[COUNT])
    analyst = Analyst(llm, db)

    assert run_due(store, analyst, notifier) == []  # not due yet
    clock["now"] = at(2026, 10, 23, 3, 5)
    assert run_due(store, analyst, notifier) == [f"{alert.id}: sent"]
    [sent] = out.telegram
    assert sent["chat_id"] == 42 and "Alert: the answer is" in sent["text"] and "How many orders?" in sent["text"]
    assert run_due(store, analyst, notifier) == []  # claimed: the same hour does not run it again

    clock["now"] = at(2026, 10, 24, 3, 5)
    assert run_due(store, analyst, notifier) == [f"{alert.id}: quiet ({orders:g})"]  # still over: no second alert
    [listed] = store.list(me.id)
    assert listed.last_status == "quiet" and listed.last_value == orders and listed.next_run_at == at(2026, 10, 25, 3)


def test_an_alert_that_drops_back_fires_again(world, db, app_tables):
    store, out, notifier, me, clock = world
    orders = db.run(COUNT).rows[0][0]
    alert = store.create(me.id, ScheduleRequestV1(question="How many orders?", kind="alert", channel="telegram",
                                                  to="42", alert={"op": "<", "value": orders + 1}))
    with app_tables.begin() as conn:  # as if it had fired before and then gone quiet
        conn.execute(text("UPDATE app.schedules SET firing = false WHERE id = :id"), {"id": alert.id})
    clock["now"] = at(2026, 10, 23, 3, 5)
    run_due(store, Analyst(FakeLLM(sql=[COUNT]), db), notifier)
    assert len(out.telegram) == 1


def test_a_weekly_report_is_emailed_and_failures_are_recorded(world, db):
    store, out, notifier, me, clock = world
    report = store.create(me.id, ScheduleRequestV1(question="How many orders?", every="week", weekday=4, hour_utc=3,
                                                   channel="email", to="me@example.com"))
    broken = store.create(me.id, ScheduleRequestV1(question="How many orders?", channel="telegram", to="7"))
    out.telegram_ok = False
    clock["now"] = at(2026, 10, 23, 3, 30)
    lines = run_due(store, Analyst(FakeLLM(sql=[COUNT]), db), notifier, "https://app.example")
    assert sorted(lines) == sorted([f"{report.id}: sent", f"{broken.id}: failed"])
    [mail] = out.email
    assert mail["to"] == ["me@example.com"] and mail["subject"] == "Your weekly report: How many orders?"
    assert "<pre" in mail["html"] and '<a href="https://app.example">' in mail["html"]
    statuses = {s.id: s.last_status for s in store.list(me.id)}
    assert statuses[report.id] == "sent" and statuses[broken.id].startswith("failed: RuntimeError")


def test_one_users_schedules_are_limited(world):
    store, _, _, me, _ = world
    for _ in range(PER_USER):
        assert store.create(me.id, ScheduleRequestV1(question="Revenue", channel="telegram", to="1"))
    assert store.create(me.id, ScheduleRequestV1(question="Revenue", channel="telegram", to="1")) is None


@pytest.fixture
def client(db, app_tables, monkeypatch):
    monkeypatch.setenv("SCHEDULES", "true")
    monkeypatch.setenv("SCHEDULE_EMAIL_ALLOWLIST", '["me@example.com"]')
    get_settings.cache_clear()
    sessions, users, out = Sessions("secret", 3600), Users(app_tables), Outbox()
    app = create_app(db_factory=lambda: db, sessions_factory=lambda: sessions, quota_factory=lambda: None,
                     users_factory=lambda: users, github_factory=lambda: object(), transcriber_factory=lambda: None,
                     schedules_factory=lambda: Schedules(app_tables),
                     notifier_factory=lambda: Notifier("re_test", "r@example.com", None, post=out.post))
    c = TestClient(app)
    me = users.sign_in(Profile(github_id=1, login="arnav", name=None, avatar_url=None))
    c.cookies.set("aa_user", sessions.signer("user").seal(str(me.id), 3600))
    yield c
    get_settings.cache_clear()


def test_the_api(client):
    made = client.post("/v1/schedules", json={"question": "Revenue yesterday", "kind": "alert", "channel": "email",
                                              "to": "me@example.com", "alert": {"op": "<", "value": 1000}})
    assert made.status_code == 201 and made.json()["alert"] == {"op": "<", "value": 1000.0}
    r = client.post("/v1/schedules", json={"question": "Revenue", "channel": "email", "to": "boss@elsewhere.com"})
    assert r.status_code == 422 and r.json()["code"] == "recipient_not_allowed"
    r = client.post("/v1/schedules", json={"question": "Revenue", "channel": "telegram", "to": "42"})
    assert r.status_code == 422 and r.json()["code"] == "channel_off"
    [listed] = client.get("/v1/schedules").json()
    assert listed["id"] == made.json()["id"] and listed["last_status"] is None
    assert client.delete(f"/v1/schedules/{listed['id']}").status_code == 204
    assert client.delete(f"/v1/schedules/{listed['id']}").status_code == 404

    client.cookies.clear()
    r = client.get("/v1/schedules")
    assert r.status_code == 401 and r.json()["code"] == "sign_in_required"


def test_without_the_feature_schedules_are_refused(db):
    get_settings.cache_clear()
    app = create_app(db_factory=lambda: db, sessions_factory=lambda: Sessions("secret", 3600),
                     quota_factory=lambda: None, github_factory=lambda: None, transcriber_factory=lambda: None)
    r = TestClient(app).get("/v1/schedules")
    assert r.status_code == 404 and r.json()["code"] == "schedules_off"


def test_the_bot_tells_a_chat_its_id(db, app_tables):
    out = Outbox()
    bot = TelegramBot(TelegramClient("t", post=out.post), lambda: None, lambda: Messages(app_tables), lambda v: None)
    bot.handle({"update_id": 1, "message": {"message_id": 1, "chat": {"id": -100123}, "text": "/id"}})
    assert "<code>-100123</code>" in out.telegram[0]["text"]
