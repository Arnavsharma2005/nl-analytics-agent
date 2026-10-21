import json

from fastapi.testclient import TestClient

from app.agents.chart import ChartSpec
from app.graph import build_graph
from app.main import create_app

from .conftest import FakeLLM
from .test_graph import REVENUE_BY_REGION


def client(db):
    graph = build_graph(FakeLLM(sql=[REVENUE_BY_REGION], chart=ChartSpec(type="bar", x="region", y=["revenue"], title="t")), db)
    return TestClient(create_app(graph_factory=lambda: graph, db_factory=lambda: db))


def test_health_schema_examples(db):
    c = client(db)
    assert c.get("/api/health").json() == {"status": "ok"}
    tables = {t["table"] for t in c.get("/api/schema").json()}
    assert tables == {"regions", "customers", "categories", "products", "orders", "order_items"}
    assert len(c.get("/api/examples").json()) >= 5


def test_ask(db):
    body = client(db).post("/api/ask", json={"question": "Revenue by region"}).json()
    assert body["chart"]["type"] == "bar" and len(body["rows"]) == 6


def test_ask_validates_input(db):
    assert client(db).post("/api/ask", json={"question": ""}).status_code == 422


def test_ask_stream(db):
    with client(db).stream("POST", "/api/ask/stream", json={"question": "Revenue by region"}) as r:
        raw = "".join(r.iter_text())
    events = [line.split(": ", 1)[1].strip() for line in raw.splitlines() if line.startswith("event:")]
    data = [json.loads(line.split(": ", 1)[1]) for line in raw.splitlines() if line.startswith("data:")]
    assert events[:2] == ["step", "step"] and events[-1] == "result"
    assert [d["node"] for d in data[:-1]] == ["router", "sql_agent", "validate", "execute", "chart_agent", "summary_agent"]
    assert data[-1]["summary"] == "Summary."
