"""The adversarial set in eval/safety.yaml: whatever SQL the model writes, nothing unsafe gets through, in PostgreSQL
(the demo) or in DuckDB (uploaded files)."""

import re
from pathlib import Path

import pytest
import yaml

from app.analyst import Analyst
from app.datasets.duck import DuckDBSource
from app.db import Database

from .conftest import TEST_DB, FakeLLM

CASES = yaml.safe_load((Path(__file__).resolve().parents[3] / "eval" / "safety.yaml").read_text())
MAX_ROWS = 50
UNMASKED_EMAIL = re.compile(r"[A-Za-z0-9._%+-]{2,}@")
UNMASKED_PHONE = re.compile(r"\+\d[\d\s().-]{8,}")


@pytest.fixture(scope="module")
def source(db):
    # The production limits, with a short timeout so runaway queries fail fast.
    return Database(TEST_DB, statement_timeout_ms=1500, max_rows=MAX_ROWS, max_cost=1_000_000,
                    personal_columns=["customers.email"])


@pytest.fixture(scope="module")
def duck(demo_parquet):
    # The demo data as an uploaded file would be held, with the same limits.
    source = DuckDBSource.build(demo_parquet, personal={("customers", "email")}, statement_timeout_ms=1500,
                                max_rows=MAX_ROWS)
    yield source
    source.close()


@pytest.fixture(scope="module")
def counts(source):
    def snapshot():
        return source.run("SELECT (SELECT COUNT(*) FROM orders), (SELECT COUNT(*) FROM customers), "
                          "(SELECT COUNT(*) FROM regions), (SELECT COUNT(*) FROM order_items)").rows

    before = snapshot()
    yield
    assert snapshot() == before, "the safety suite changed data"


TARGETS = [("postgres", c, c["expect"]) for c in CASES if c.get("only") != "duckdb"] + \
          [("duckdb", c, c.get("duckdb", c["expect"])) for c in CASES]


@pytest.mark.parametrize(("target", "case", "expect"), TARGETS, ids=[f"{t}-{c['id']}" for t, c, _ in TARGETS])
def test_safety(target, case, expect, request, counts):
    source = request.getfixturevalue("source" if target == "postgres" else "duck")
    out = Analyst(FakeLLM(sql=[case["sql"]]), source).ask("Please answer this")
    cells = [str(v) for row in out.rows for v in row] + [out.summary or ""]
    if expect == "blocked":
        assert out.error and out.error.startswith("I couldn't build a working query"), out.model_dump()
        assert out.rows == []
    elif expect == "masked":
        assert out.error is None and out.rows
        assert not any(UNMASKED_EMAIL.search(c) or UNMASKED_PHONE.search(c) for c in cells), out.rows[:3]
    elif expect == "bounded":
        assert out.error is None and len(out.rows) == MAX_ROWS and out.truncated
    else:
        raise AssertionError(f"unknown expectation {expect}")
