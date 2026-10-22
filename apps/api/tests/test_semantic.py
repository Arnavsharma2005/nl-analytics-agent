"""The semantic layer: business rules and verified queries, and the checks that keep the library honest."""

import re
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from app import semantic
from app.db import Database
from app.evaluation import results_match
from app.sql_guard import validate_sql

from .conftest import TEST_DB

QUESTIONS = yaml.safe_load((Path(__file__).resolve().parents[3] / "eval" / "questions.yaml").read_text())
HELD_OUT = [q for q in QUESTIONS if q["split"] == "test"]
LIBRARY = semantic.load()
NEAR_DUPLICATE = 0.85  # token overlap above which two questions count as the same question reworded slightly


def words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def overlap(a: str, b: str) -> float:
    a, b = words(a), words(b)
    return len(a & b) / len(a | b)


@pytest.fixture(scope="module")
def source(db):
    # The production guard and cost ceiling; no row limit, so whole results can be compared.
    return Database(TEST_DB, max_rows=100_000, max_cost=1_000_000, personal_columns=["customers.email"])


@pytest.fixture(scope="module")
def held_out_results(source):
    return [(q, source.run(q["gold_sql"]).rows) for q in HELD_OUT]


@pytest.mark.parametrize("query", LIBRARY.queries, ids=[q.question for q in LIBRARY.queries])
def test_verified_queries_pass_the_guard_and_run(query, source):
    schema = source.schema()
    validate_sql(query.sql, set(schema.tables), schema.personal_columns())
    assert source.run(query.sql).rows, "a verified query should return something"


@pytest.mark.parametrize("query", LIBRARY.queries, ids=[q.question for q in LIBRARY.queries])
def test_verified_queries_do_not_give_away_held_out_questions(query, source, held_out_results):
    rows = source.run(query.sql).rows
    same_answer = [q["id"] for q, gold in held_out_results if results_match(gold, rows)]
    assert not same_answer, f"returns the answer to held-out eval question(s) {same_answer}"
    reworded = [q["id"] for q in HELD_OUT if overlap(q["question"], query.question) >= NEAR_DUPLICATE]
    assert not reworded, f"is nearly the same question as held-out eval question(s) {reworded}"


def test_the_library_offers_starter_questions():
    starters = [q.question for q in LIBRARY.queries if q.starter]
    assert 5 <= len(starters) <= 10


def test_rules_render_as_a_bulleted_list():
    rules = semantic.Semantic(rules=[
        {"id": "a", "text": "Revenue is money in,\nfrom completed orders.\n", "terms": ["revenue"]},
        {"id": "b", "text": "Dates are UTC."},
    ])
    assert rules.rules_text() == "- Revenue is money in,\n  from completed orders.\n- Dates are UTC."
    assert rules.rules[1].terms == ()


def test_every_shipped_rule_is_in_the_prompt():
    from app.agents.sql_agent import write_sql
    from app.brief import demo

    from .conftest import FakeLLM

    llm = FakeLLM()
    write_sql(llm, "Revenue by region", "", [], demo())
    (_, prompt), = llm.systems
    assert all(rule.text.splitlines()[0] in prompt for rule in LIBRARY.rules)


@pytest.mark.parametrize("bad", [
    {"rules": [{"id": "a", "text": "x"}, {"id": "a", "text": "y"}]},
    {"rules": [{"id": "a", "text": "one\n\ntwo"}]},
    {"rules": [{"id": "a", "text": "x", "colour": "red"}]},
    {"rules": [], "queries": [{"question": "q", "sql": "SELECT 1", "verified_by": "me"}]},
    {"rules": [], "queries": [{"question": "Q", "sql": "SELECT 1", "verified_by": "me", "verified_at": "2026-01-01"},
                              {"question": "q", "sql": "SELECT 2", "verified_by": "me", "verified_at": "2026-01-01"}]},
])
def test_malformed_files_are_rejected(bad):
    with pytest.raises(ValidationError):
        semantic.Semantic(**bad)
