"""The adversarial set in eval/safety.yaml: whatever SQL the model writes, nothing unsafe gets through."""

import re
from pathlib import Path

import pytest
import yaml

from app.analyst import Analyst
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
def counts(source):
    def snapshot():
        return source.run("SELECT (SELECT COUNT(*) FROM orders), (SELECT COUNT(*) FROM customers), "
                          "(SELECT COUNT(*) FROM regions), (SELECT COUNT(*) FROM order_items)").rows

    before = snapshot()
    yield
    assert snapshot() == before, "the safety suite changed data"


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_safety(case, source, counts):
    out = Analyst(FakeLLM(sql=[case["sql"]]), source).ask("Please answer this")
    cells = [str(v) for row in out.rows for v in row] + [out.summary or ""]
    if case["expect"] == "blocked":
        assert out.error and out.error.startswith("I couldn't build a working query"), out.model_dump()
        assert out.rows == []
    elif case["expect"] == "masked":
        assert out.error is None and out.rows
        assert not any(UNMASKED_EMAIL.search(c) or UNMASKED_PHONE.search(c) for c in cells), out.rows[:3]
    elif case["expect"] == "bounded":
        assert out.error is None and len(out.rows) == MAX_ROWS and out.truncated
    else:
        raise AssertionError(f"unknown expectation {case['expect']}")
