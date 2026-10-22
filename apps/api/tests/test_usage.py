"""Sessions, the bot check, quotas, model budgets and the answer cache."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.agents.chart import ChartSpec
from app.analyst import Analyst, Answer, Chart, Done, Step, Table, Token
from app.answer_cache import AnswerCache, normalise
from app.llm import AllProvidersFailed, FallbackLLM, Provider
from app.main import create_app
from app.sessions import COOKIE, Sessions, Turnstile
from app.usage import Counters, DailyBudget, Quota, Visitor

from .conftest import FakeLLM
from .test_graph import REVENUE_BY_REGION


class Clock:
    def __init__(self, at: datetime):
        self.at = at

    def __call__(self) -> datetime:
        return self.at


# --- counters, quotas, budgets ------------------------------------------------------------------------------

def test_counters_stop_at_the_limit_and_reset_with_the_window(app_tables):
    clock = Clock(datetime(2026, 10, 22, 10, 59, 0, tzinfo=UTC))
    counters = Counters(app_tables, clock)
    assert [counters.spend("k", 3600, 2).allowed for _ in range(3)] == [True, True, False]
    assert counters.spend("k", 3600, 2).retry_after_s == 60
    assert counters.spend("other", 3600, 2).allowed
    clock.at += timedelta(minutes=1)  # 11:00, a new hourly window
    assert counters.spend("k", 3600, 2).allowed


def test_quota_counts_sessions_and_ips_separately(app_tables):
    quota = Quota(Counters(app_tables), per_session_hour=2, per_ip_day=3)
    assert quota.spend(Visitor("a", "1.1.1.1")) is None
    assert quota.spend(Visitor("a", "1.1.1.1")) is None
    assert quota.spend(Visitor("a", "1.1.1.1")).retry_after_s <= 3600  # session a is out
    assert quota.spend(Visitor("b", "1.1.1.1")) is None  # a new session, but the IP's third question
    assert quota.spend(Visitor("c", "1.1.1.1")).retry_after_s > 0  # the IP is out for the day
    assert quota.spend(Visitor(None, "2.2.2.2")) is None  # no session: only the IP limit applies


def test_quota_fails_open_without_the_app_database():
    from sqlalchemy import create_engine

    broken = Counters(create_engine("postgresql+psycopg://nobody@127.0.0.1:1/none"))
    assert Quota(broken, 1, 1).spend(Visitor("a", "ip")) is None
    assert DailyBudget(broken, {"groq": 1}).spend("groq")


class Reply:
    def __init__(self, content):
        self.content = content


def test_providers_over_budget_are_skipped(app_tables):
    calls = []

    class Client:
        def __init__(self, name):
            self.name = name

        def invoke(self, messages):
            calls.append(self.name)
            return Reply("fine")

    budget = DailyBudget(Counters(app_tables), {"groq": 1})
    llm = FallbackLLM([Provider("groq", "Groq", "u", "m", "k"), Provider("mistral", "Mistral", "u", "m", "k")],
                      client_factory=lambda p, t: Client(p.name), budget=budget.spend)
    assert [llm.text("s", "u") for _ in range(2)] == ["fine", "fine"]
    assert calls == ["groq", "mistral"]


def test_all_budgets_spent_is_its_own_error(app_tables):
    budget = DailyBudget(Counters(app_tables), {"groq": 1})
    budget.spend("groq")
    llm = FallbackLLM([Provider("groq", "Groq", "u", "m", "k")], client_factory=lambda p, t: None, budget=budget.spend)
    with pytest.raises(AllProvidersFailed) as failed:
        llm.text("s", "u")
    assert failed.value.daily_budget_used


# --- sessions and the bot check ------------------------------------------------------------------------------

def test_session_cookies_are_signed_and_expire():
    now = [1_000_000.0]
    sessions = Sessions("secret", ttl_s=60, clock=lambda: now[0])
    value = sessions.issue(None, "ip")
    sid = sessions.read(value)
    assert sid and not sessions.required
    assert sessions.read(value.replace(sid, "someone-else")) is None
    assert Sessions("other-secret", 60, clock=lambda: now[0]).read(value) is None
    assert sessions.read("garbage") is None and sessions.read(None) is None
    now[0] += 61
    assert sessions.read(value) is None


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def json(self):
        return self.body


def test_turnstile_gates_sessions():
    seen = []

    def post(url, data, timeout):
        seen.append(data)
        return FakeResponse({"success": data["response"] == "good", "error-codes": ["invalid-input-response"]})

    sessions = Sessions("secret", 60, Turnstile("ts-secret", post))
    assert sessions.required
    assert sessions.issue("bad", "1.2.3.4") is None
    assert sessions.issue(None, "1.2.3.4") is None  # no token: Cloudflare is not even asked
    assert sessions.read(sessions.issue("good", "1.2.3.4"))
    assert seen[0] == {"secret": "ts-secret", "response": "bad", "remoteip": "1.2.3.4"} and len(seen) == 2


def make_client(db, sessions, quota=None, analyst=None):
    analyst = analyst or Analyst(FakeLLM(sql=[REVENUE_BY_REGION]), db)
    return TestClient(create_app(analyst_factory=lambda: analyst, db_factory=lambda: db,
                                 sessions_factory=lambda: sessions, quota_factory=lambda: quota))


def test_api_requires_a_session_when_the_bot_check_is_on(db, monkeypatch):
    monkeypatch.setenv("TURNSTILE_SITE_KEY", "site-key")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        post = lambda url, data, timeout: FakeResponse({"success": data["response"] == "good"})  # noqa: E731
        c = make_client(db, Sessions("secret", 60, Turnstile("ts", post)))
        assert c.get("/api/config").json() == {"turnstile_site_key": "site-key"}
        r = c.post("/api/ask", json={"question": "Revenue by region"})
        assert r.status_code == 403 and r.json()["code"] == "bot_check_required"
        r = c.post("/api/session", json={"turnstile_token": "bad"})
        assert r.status_code == 403 and r.json()["code"] == "bot_check_failed"
        r = c.post("/api/session", json={"turnstile_token": "good"})
        assert r.status_code == 200 and COOKIE in r.cookies
        assert "httponly" in r.headers["set-cookie"].lower() and "samesite=lax" in r.headers["set-cookie"].lower()
        assert c.post("/api/ask", json={"question": "Revenue by region"}).status_code == 200
    finally:
        get_settings.cache_clear()


def test_without_the_bot_check_no_session_is_needed(db):
    c = make_client(db, Sessions("secret", 60))
    assert c.get("/api/config").json() == {"turnstile_site_key": None}
    assert c.post("/api/ask", json={"question": "Revenue by region"}).status_code == 200
    assert c.post("/api/session", json={}).status_code == 200


def test_quota_exceeded_is_429_with_retry_after(db, app_tables):
    c = make_client(db, Sessions("secret", 60), Quota(Counters(app_tables), per_session_hour=1, per_ip_day=0))
    c.post("/api/session", json={})
    assert c.post("/api/ask", json={"question": "Revenue by region"}).status_code == 200
    r = c.post("/api/ask/stream", json={"question": "Revenue by region"})
    assert r.status_code == 429 and r.json()["code"] == "quota_exceeded"
    assert 0 < int(r.headers["retry-after"]) <= 3600 and r.json()["retry_after"] == int(r.headers["retry-after"])


def test_client_ip_comes_from_the_configured_header(db, app_tables, monkeypatch):
    monkeypatch.setenv("CLIENT_IP_HEADER", "true-client-ip")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        c = make_client(db, Sessions("secret", 60), Quota(Counters(app_tables), per_session_hour=0, per_ip_day=1))
        ask = lambda ip: c.post("/api/ask", json={"question": "Revenue by region"}, headers={"true-client-ip": ip})  # noqa: E731
        assert ask("9.9.9.9").status_code == 200
        assert ask("9.9.9.9").status_code == 429
        assert ask("8.8.8.8").status_code == 200
    finally:
        get_settings.cache_clear()


# --- the answer cache ----------------------------------------------------------------------------------------

def chart_llm():
    return FakeLLM(sql=[REVENUE_BY_REGION], chart=ChartSpec(type="bar", x="region", y=["revenue"], title="t"))


def test_normalise():
    assert normalise("  Revenue   by REGION?? ") == normalise("revenue by region") == "revenue by region"


def test_repeat_questions_are_served_from_the_cache(db, app_tables):
    llm = chart_llm()
    analyst = Analyst(llm, db, cache=AnswerCache(app_tables, "v1", ttl_s=3600))
    first = analyst.ask("Revenue by region?")
    calls = len(llm.calls)
    again = analyst.ask("  revenue BY region ")
    assert len(llm.calls) == calls  # no model calls the second time
    assert again.cached and not first.cached and again.question == "revenue BY region"
    assert again.rows == first.rows and again.chart == first.chart and again.timings == {}


def test_a_new_version_or_an_old_entry_is_a_miss(db, app_tables):
    Analyst(chart_llm(), db, cache=AnswerCache(app_tables, "v1", 3600)).ask("Revenue by region")
    assert AnswerCache(app_tables, "v2", 3600).get("Revenue by region") is None
    assert AnswerCache(app_tables, "v1", 3600).get("Revenue by region")
    with app_tables.begin() as conn:
        conn.execute(text("UPDATE app.answer_cache SET created_at = now() - interval '2 hours'"))
    assert AnswerCache(app_tables, "v1", 3600).get("Revenue by region") is None


def test_failed_or_partial_answers_are_not_cached(db, app_tables):
    cache = AnswerCache(app_tables, "v1", 3600)
    Analyst(FakeLLM(sql=["DELETE FROM orders"]), db, cache=cache).ask("delete everything")
    Analyst(FakeLLM(intent="out_of_scope"), db, cache=cache).ask("weather?")

    class NoSummary(FakeLLM):
        def text(self, system, user, tier="strong"):
            raise AllProvidersFailed([{"provider": "x", "error": "rate limited"}])

    Analyst(NoSummary(sql=[REVENUE_BY_REGION]), db, cache=cache).ask("Revenue by region")
    with app_tables.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM app.answer_cache")).scalar() == 0


def test_stream_serves_cache_hits(db, app_tables):
    analyst = Analyst(chart_llm(), db, cache=AnswerCache(app_tables, "v1", 3600))

    async def collect():
        return [e async for e in analyst.stream("Revenue by region")]

    first = asyncio.run(collect())
    second = asyncio.run(collect())
    assert len(first) > 2 and not first[-1].answer.cached
    hit = second[-1].answer
    assert second == [Step("cache", "Found a recent answer to the same question"),
                      Table(hit.columns, hit.rows, hit.truncated), Chart(hit.chart), Token(hit.summary, replace=True),
                      Done(hit)]
    assert isinstance(second[-1].answer, Answer) and second[-1].answer.cached
