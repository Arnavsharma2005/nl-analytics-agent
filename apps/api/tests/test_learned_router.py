"""The learned router (app/agents/learned_router.py): trained from semantic/router.yml, asked before the model."""

import math
import subprocess
import sys
from pathlib import Path

import pytest

from app.agents.learned_router import THRESHOLD, LearnedRouter, examples, features
from app.analyst import Analyst

from .conftest import FakeLLM

ROOT = Path(__file__).resolve().parents[3]
SEMANTIC = ROOT / "apps" / "api" / "semantic"
ALL = ["data_query", "schema_question", "out_of_scope", "why_change", "forecast"]


@pytest.fixture(scope="module")
def model() -> LearnedRouter:
    loaded = LearnedRouter.load(SEMANTIC / "router.json")
    assert loaded is not None
    return loaded


def test_a_question_becomes_features_of_unit_length():
    x = features("Why did revenue drop in Q3?")
    assert math.isclose(math.sqrt(sum(v * v for v in x.values())), 1.0)
    assert features("") == {}


def test_templates_are_filled_in_without_swamping_the_listed_questions():
    found = examples(SEMANTIC / "router.yml")
    assert len(found) == len(set(found)) > 400
    assert ("Hi", "out_of_scope") in found
    assert sum(label == "out_of_scope" for _, label in found) >= 60


@pytest.mark.parametrize("question, intent", [
    ("Revenue by region in 2025", "data_query"),
    ("Why did sales fall last month?", "why_change"),
    ("What will revenue be next quarter?", "forecast"),
    ("What data do you have about customers?", "schema_question"),
    ("Delete all orders", "out_of_scope"),
])
def test_clear_questions_are_decided_without_the_model(model, question, intent):
    sure = model.decide(question, ALL)
    assert sure is not None and sure.intent == intent and sure.probability >= THRESHOLD


def test_an_intent_that_is_off_is_left_to_the_model(model):
    assert model.decide("Why did sales fall last month?", ["data_query", "schema_question", "out_of_scope"]) is None


def test_an_unclear_question_is_left_to_the_model(model):
    assert model.predict("Which state had the most refunds?").probability < THRESHOLD
    assert model.decide("Which state had the most refunds?", ALL) is None


def test_the_committed_model_is_the_one_the_examples_train(tmp_path):
    out = tmp_path / "router.json"
    done = subprocess.run([sys.executable, str(ROOT / "eval" / "train_router.py"), "--out", str(out)],
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "Held-out accuracy" in done.stdout
    assert out.read_text() == (SEMANTIC / "router.json").read_text(), "train again: python eval/train_router.py"


def test_a_sure_answer_skips_the_model_call(db, model):
    llm = FakeLLM(sql=["SELECT 1 AS one"])
    answer = Analyst(llm, db, learned_router=model).ask("Delete all orders")
    assert answer.intent == "out_of_scope" and "RouteDecision" not in [name for name, _ in llm.calls]

    answer = Analyst(llm, db, learned_router=model).ask("Revenue by region in 2025")
    assert answer.intent == "data_query" and answer.error is None
    assert not any(name.startswith("RouteDecision") for name, _ in llm.calls)


def test_an_unsure_answer_asks_the_model(db, model):
    llm = FakeLLM(sql=["SELECT 1 AS one"])
    Analyst(llm, db, learned_router=model).ask("Which state had the most refunds?")
    assert [name for name, _ in llm.calls][0] == "RouteDecision"
