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

    def __init__(self, intent="data_query", sql=("SELECT 1 AS n",), chart=None, summary="Summary.", schema_answer="We have sales data.",
                 assumptions=(), standalone=None):
        self.intent = intent
        self.sql = list(sql)
        self.chart = chart
        self.summary = summary
        self.schema_answer = schema_answer
        self.assumptions = list(assumptions)
        self.standalone = standalone  # the rewritten follow-up; None returns the latest message unchanged
        self.systems: list[tuple[str, str]] = []
        self.calls: list[tuple[str, str]] = []
        self.tiers: list[tuple[str, str]] = []

    def structured(self, output: type[BaseModel], system: str, user: str, tier: str = "strong"):
        self.calls.append((output.__name__, user))
        self.tiers.append((output.__name__, tier))
        self.systems.append((output.__name__, system))
        if output is RouteDecision:
            return RouteDecision(intent=self.intent, reason="test")
        if issubclass(output, SQLDraft):
            n = sum(1 for name, _ in self.calls if name.endswith("SQLDraft"))
            extra = {"assumptions": self.assumptions} if output is not SQLDraft else {}
            return output(reasoning="test", sql=self.sql[min(n, len(self.sql)) - 1], **extra)
        if output.__name__ == "Standalone":
            return output(question=self.standalone or user.rsplit("Latest message: ", 1)[1])
        if output.__name__ == "Explanation":
            return output(steps=["Add up the sales of completed orders.", "Split them by region."])
        if output is ChartSpec:
            if self.chart is None:
                raise RuntimeError("no chart scripted")
            return self.chart
        raise AssertionError(f"unexpected structured call {output}")

    def text(self, system: str, user: str, tier: str = "strong") -> str:
        self.calls.append(("text", user))
        self.tiers.append(("text", tier))
        if "help business users understand" in system:
            return self.schema_answer
        # A list of summaries is returned one per call (the last one repeats).
        if isinstance(self.summary, list):
            n = sum(1 for name, u in self.calls if name == "text" and u.startswith("Question:"))
            return self.summary[min(n, len(self.summary)) - 1]
        return self.summary

    def stream_text(self, system: str, user: str, tier: str = "strong"):
        text = self.text(system, user, tier=tier)
        half = len(text) // 2
        yield from (text[:half], text[half:])


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
        conn.execute(text("TRUNCATE app.usage_counters, app.answer_cache, app.messages, app.feedback, app.users, app.datasets, app.shares, app.curation"))
    return app_engine


DEMO_TABLES = ["regions", "customers", "categories", "products", "orders", "order_items"]


def demo_as_parquet(db) -> dict[str, bytes]:
    """The demo tables as Parquet files, for building a DuckDB copy of the demo data."""
    import tempfile
    from pathlib import Path

    import duckdb

    out = {}
    with tempfile.TemporaryDirectory() as tmp, duckdb.connect() as duck:
        raw = db.engine.raw_connection()
        try:
            for table in DEMO_TABLES:
                csv = Path(tmp) / f"{table}.csv"
                with raw.cursor() as cur, cur.copy(f"COPY {table} TO STDOUT WITH CSV HEADER") as copy, \
                        csv.open("wb") as f:
                    for chunk in copy:
                        f.write(chunk)
                parquet = Path(tmp) / f"{table}.parquet"
                duck.execute(f"COPY (SELECT * FROM read_csv('{csv}', header = true)) TO '{parquet}' (FORMAT parquet)")
                out[table] = parquet.read_bytes()
        finally:
            raw.close()
    return out


@pytest.fixture(scope="session")
def demo_parquet(db):
    return demo_as_parquet(db)
