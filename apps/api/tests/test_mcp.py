"""The MCP server at /mcp: the analyst as a tool for agents, behind the same guard and limits as the web app."""

import pytest
from fastapi.testclient import TestClient

from app import semantic
from app.agents.chart import ChartSpec
from app.analyst import Analyst
from app.config import get_settings
from app.db import Database
from app.llm import AllProvidersFailed
from app.main import create_app
from app.sessions import Sessions
from app.usage import Counters, Quota

from .conftest import TEST_DB, FakeLLM
from .test_graph import REVENUE_BY_REGION

HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
           "MCP-Protocol-Version": "2025-06-18"}


@pytest.fixture
def make(db, app_tables, monkeypatch):
    monkeypatch.setenv("MCP", "true")
    get_settings.cache_clear()
    llm = FakeLLM(sql=[REVENUE_BY_REGION], chart=ChartSpec(type="bar", x="region", y=["revenue"], title="t"),
                  summary="North leads.")
    clients = []

    def build(quota=None, analyst=None, source=None):
        app = create_app(analyst_factory=lambda: analyst or Analyst(llm, db), db_factory=lambda: source or db,
                         llm_factory=lambda: llm, sessions_factory=lambda: Sessions("secret", 3600),
                         quota_factory=lambda: quota, github_factory=lambda: None, transcriber_factory=lambda: None)
        client = TestClient(app).__enter__()  # runs the lifespan, which starts the MCP session manager
        clients.append(client)
        return client

    build.llm = llm
    yield build
    for client in clients:
        client.__exit__(None, None, None)
    get_settings.cache_clear()


def rpc(client: TestClient, method: str, params: dict | None = None) -> dict:
    r = client.post("/mcp", headers=HEADERS, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
    assert r.status_code == 200, r.text
    return r.json()["result"]


def call(client: TestClient, tool: str, **arguments) -> dict:
    return rpc(client, "tools/call", {"name": tool, "arguments": arguments})


def test_the_tools_are_listed_as_read_only(make):
    tools = {t["name"]: t for t in rpc(make(), "tools/list")["tools"]}
    assert set(tools) == {"ask", "list_tables", "list_verified_questions", "run_verified_query"}
    assert all(t["annotations"]["readOnlyHint"] for t in tools.values())
    assert tools["ask"]["inputSchema"]["required"] == ["question"]


def test_a_question_is_answered_with_its_sql_and_result(make):
    result = call(make(), "ask", question="Revenue by region")
    assert not result.get("isError")
    answer = result["structuredContent"]
    assert answer["answer"] == "North leads." and "SUM(oi.quantity * oi.unit_price)" in answer["sql"]
    assert answer["result"]["columns"] == ["region", "revenue"] and answer["result"]["total_rows"] > 0


def test_a_verified_query_runs_as_written_without_a_model(make):
    client = make()
    questions = call(client, "list_verified_questions")["structuredContent"]["result"]
    query = semantic.load().queries[1]
    assert query.question in questions
    calls = len(make.llm.calls)
    result = call(client, "run_verified_query", question=query.question.upper())["structuredContent"]
    assert result["columns"] and result["rows"] and len(make.llm.calls) == calls

    unknown = call(client, "run_verified_query", question="What is the meaning of life?")
    assert unknown["isError"] and "list_verified_questions" in unknown["content"][0]["text"]


def test_the_tables_are_described_with_personal_columns_marked(make):
    source = Database(TEST_DB, personal_columns=["customers.email"])
    tables = {t["name"]: t for t in call(make(source=source), "list_tables")["structuredContent"]["result"]}
    email = next(c for c in tables["customers"]["columns"] if c["name"] == "email")
    assert email["personal"] is True and "orders" in tables


def test_questions_count_against_the_ip_limit(make, app_tables):
    client = make(quota=Quota(Counters(app_tables), per_session_hour=1, per_ip_day=1))
    assert not call(client, "ask", question="Revenue by region").get("isError")
    refused = call(client, "ask", question="Revenue by region")
    assert refused["isError"] and "question limit" in refused["content"][0]["text"]
    assert call(client, "run_verified_query", question=semantic.load().queries[0].question)["isError"]
    assert not call(client, "list_tables").get("isError")  # describing the data is free


def test_a_failure_to_answer_is_reported_to_the_agent(make, db):
    class Down:
        def ask(self, question, history=()):
            raise AllProvidersFailed([{"provider": "groq", "error": "rate limited"}])

    result = call(make(analyst=Down()), "ask", question="Revenue by region")
    assert result["isError"] and "provider" in result["content"][0]["text"].lower()


def test_the_mcp_server_is_off_unless_asked_for(db, monkeypatch):
    monkeypatch.delenv("MCP", raising=False)
    get_settings.cache_clear()
    app = create_app(db_factory=lambda: db, sessions_factory=lambda: Sessions("secret", 3600),
                     quota_factory=lambda: None, github_factory=lambda: None, transcriber_factory=lambda: None)
    assert TestClient(app).post("/mcp", headers=HEADERS, json={}).status_code in (404, 405)
