import os
from pathlib import Path

import pytest
from pydantic import BaseModel

from app.agents.chart import ChartSpec
from app.agents.router import RouteDecision
from app.agents.sql_agent import SQLDraft

TEST_DB = os.environ.get("TEST_DATABASE_URL", os.environ.get("DATABASE_URL", "postgresql+psycopg://postgres@localhost:5432/analytics"))


class FakeLLM:
    """Scripted stand-in for a chat model.

    `sql` is a list of queries returned on successive SQL-agent calls (the last one repeats)."""

    def __init__(self, intent="data_query", sql=("SELECT 1 AS n",), chart=None, summary="Summary.", schema_answer="We have sales data."):
        self.intent = intent
        self.sql = list(sql)
        self.chart = chart
        self.summary = summary
        self.schema_answer = schema_answer
        self.calls: list[tuple[str, str]] = []

    def structured(self, output: type[BaseModel], system: str, user: str):
        self.calls.append((output.__name__, user))
        if output is RouteDecision:
            return RouteDecision(intent=self.intent, reason="test")
        if output is SQLDraft:
            n = sum(1 for name, _ in self.calls if name == "SQLDraft")
            return SQLDraft(reasoning="test", sql=self.sql[min(n, len(self.sql)) - 1])
        if output is ChartSpec:
            if self.chart is None:
                raise RuntimeError("no chart scripted")
            return self.chart
        raise AssertionError(f"unexpected structured call {output}")

    def text(self, system: str, user: str) -> str:
        self.calls.append(("text", user))
        return self.schema_answer if "help business users understand" in system else self.summary


@pytest.fixture(scope="session")
def db():
    from sqlalchemy import text

    from app.db import Database
    from app.seed import seed

    database = Database(TEST_DB)
    try:
        with database.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"PostgreSQL not available at {TEST_DB}: {e}")
    seed(TEST_DB)
    return database


@pytest.fixture(scope="session")
def app_engine(db):
    """The app's own tables, migrated to the latest version in the test database."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine

    from app.config import get_settings

    get_settings.cache_clear()
    os.environ["APP_DATABASE_URL"] = TEST_DB
    try:
        command.upgrade(Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head")
    finally:
        del os.environ["APP_DATABASE_URL"]
        get_settings.cache_clear()
    return create_engine(TEST_DB)


@pytest.fixture
def app_tables(app_engine):
    """Empty app tables for one test."""
    from sqlalchemy import text

    with app_engine.begin() as conn:
        conn.execute(text("TRUNCATE app.usage_counters, app.answer_cache"))
    return app_engine
