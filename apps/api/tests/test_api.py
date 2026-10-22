import json

from fastapi.testclient import TestClient

from app.agents.chart import ChartSpec
from app.analyst import Analyst
from app.config import get_settings
from app.main import create_app
from app.sessions import Sessions

from .conftest import FakeLLM
from .test_graph import REVENUE_BY_REGION


def client(db, sessions=None, quota=None):
    analyst = Analyst(FakeLLM(sql=[REVENUE_BY_REGION], chart=ChartSpec(type="bar", x="region", y=["revenue"], title="t")), db)
    sessions = sessions or Sessions("test-secret", 3600)
    return TestClient(create_app(analyst_factory=lambda: analyst, db_factory=lambda: db,
                                 sessions_factory=lambda: sessions, quota_factory=lambda: quota))


def test_health_schema_examples(db):
    c = client(db)
    assert c.get("/api/health").json() == {"status": "ok"}
    tables = {t["table"] for t in c.get("/api/schema").json()}
    assert tables == {"regions", "customers", "categories", "products", "orders", "order_items"}
    assert len(c.get("/api/examples").json()) >= 5


def test_healthz_never_touches_the_database(monkeypatch):
    monkeypatch.setenv("RENDER_GIT_COMMIT", "abc123")
    get_settings.cache_clear()
    try:
        def no_db():
            raise AssertionError("/healthz must not open a database connection")

        c = TestClient(create_app(analyst_factory=no_db, db_factory=no_db, sessions_factory=no_db, quota_factory=no_db))
        assert c.get("/healthz").json() == {"status": "ok", "commit": "abc123"}
        r = c.get("/readyz")
        assert r.status_code == 503 and r.json()["code"] == "database_unavailable" and "AssertionError" not in r.text
    finally:
        get_settings.cache_clear()


def test_readyz(db):
    assert client(db).get("/readyz").json() == {"status": "ok"}


def test_ask(db):
    body = client(db).post("/api/ask", json={"question": "Revenue by region"}).json()
    assert body["chart"]["type"] == "bar" and len(body["rows"]) == 6


def test_ask_validates_input(db):
    r = client(db).post("/api/ask", json={"question": ""})
    assert r.status_code == 422 and r.headers["content-type"] == "application/problem+json"
    assert r.json()["code"] == "invalid_request" and r.json()["errors"][0]["field"] == "question"


def test_unexpected_errors_hide_their_message(db):
    class Broken:
        def ask(self, question):
            raise RuntimeError("password=hunter2 at db.internal")

        async def stream(self, question):
            raise RuntimeError("password=hunter2 at db.internal")
            yield

    c = TestClient(create_app(analyst_factory=Broken, db_factory=lambda: db, sessions_factory=lambda: Sessions("s", 60),
                              quota_factory=lambda: None), raise_server_exceptions=False)
    r = c.post("/api/ask", json={"question": "Revenue by region"})
    assert r.status_code == 500 and r.json()["code"] == "internal_error" and "hunter2" not in r.text
    with c.stream("POST", "/api/ask/stream", json={"question": "Revenue by region"}) as s:
        raw = "".join(s.iter_text())
    assert "event: error" in raw and "internal_error" in raw and "hunter2" not in raw


def test_ask_stream(db):
    with client(db).stream("POST", "/api/ask/stream", json={"question": "Revenue by region"}) as r:
        raw = "".join(r.iter_text())
    events = [line.split(": ", 1)[1].strip() for line in raw.splitlines() if line.startswith("event:")]
    data = [json.loads(line.split(": ", 1)[1]) for line in raw.splitlines() if line.startswith("data:")]
    assert events[:2] == ["step", "step"] and events[-1] == "result"
    assert [d["node"] for d in data[:-1]] == ["router", "sql_agent", "validate", "execute", "chart_agent", "summary_agent"]
    assert data[-1]["summary"] == "Summary."


def test_providers_endpoint_and_all_failed_is_503(db):
    from app.llm import AllProvidersFailed, FallbackLLM, Provider

    class Down:
        def invoke(self, messages):
            err = Exception("Rate limit reached")
            err.status_code = 429
            raise err

    llm = FallbackLLM([Provider("groq", "Groq · m", "https://x", "m", "k"), Provider("mistral", "Mistral · m", "https://x", "m", None)],
                      client_factory=lambda p, t: Down())
    analyst = Analyst(llm, db)
    c = TestClient(create_app(analyst_factory=lambda: analyst, db_factory=lambda: db, llm_factory=lambda: llm,
                              sessions_factory=lambda: Sessions("s", 60), quota_factory=lambda: None))
    r = c.post("/api/ask", json={"question": "Revenue by region"})
    assert r.status_code == 503 and r.json()["errors"] == [{"provider": "Groq · m", "error": "rate limited"}]
    assert r.json()["code"] == "all_providers_failed"
    status = c.get("/api/providers").json()
    assert status[0]["configured"] and status[0]["cooling_down_s"] > 0
    assert status[1] == {"name": "mistral", "label": "Mistral · m", "configured": False, "cooling_down_s": 0}
    assert "key" not in str(status)
    assert AllProvidersFailed


def test_answer_keeps_every_field_the_web_app_reads(db):
    # apps/web/src/types.ts AskResult. Fields may be added to Answer, never renamed or removed.
    body = client(db).post("/api/ask", json={"question": "Revenue by region"}).json()
    assert set(body) >= {"question", "intent", "answer", "sql", "sql_reasoning", "columns", "rows", "truncated", "chart",
                         "chart_source", "summary", "error", "attempts", "timings", "llm_calls"}
