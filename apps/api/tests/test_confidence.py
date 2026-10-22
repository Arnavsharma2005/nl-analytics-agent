"""Confidence: the signals every data answer carries, and the model trained on them (app/confidence.py)."""

import json
import random
import subprocess
import sys
from pathlib import Path

import pytest

from app.agents.chart import ChartSpec
from app.analyst import Analyst, Answer
from app.confidence import (
    ConfidenceModel,
    Isotonic,
    Signals,
    auroc,
    calibration_error,
    fit_isotonic,
    load_model,
    save_model,
    train,
)
from app.context.retrieval import ContextPack, Example

from .conftest import FakeLLM
from .test_graph import REVENUE_BY_REGION

ROOT = Path(__file__).resolve().parents[3]


def signals(**overrides) -> Signals:
    base = dict(verified=False, nearest_example=None, attempts=1, invalid_attempts=0, failed_runs=0, empty_retries=0,
                empty_result=False, rows=6, assumptions=0, follow_up=False, summary_checked=None, joins=2,
                window_functions=False, subqueries=0)
    return Signals(**{**base, **overrides})


def test_a_data_answer_carries_its_signals_and_no_label_without_a_model(db):
    llm = FakeLLM(sql=["SELECT region_name FROM orders", REVENUE_BY_REGION],
                  chart=ChartSpec(type="bar", x="region", y=["revenue"], title="t"))
    answer = Analyst(llm, db).ask("Revenue by region")
    c = answer.confidence
    assert c.probability is None and c.label is None
    assert c.signals.attempts == 2 and c.signals.invalid_attempts == 1 and c.signals.failed_runs == 0
    assert c.signals.rows == 6 and not c.signals.empty_result and c.signals.joins == 3
    assert c.signals.nearest_example is None and not c.signals.verified
    assert Answer.model_validate_json(answer.model_dump_json()).confidence == c  # stored and replayed intact


def test_only_answered_data_questions_are_assessed(db):
    assert Analyst(FakeLLM(intent="out_of_scope"), db).ask("What's the weather?").confidence is None
    assert Analyst(FakeLLM(sql=["DELETE FROM orders"]), db).ask("delete all orders").confidence is None


def test_signals_read_the_pipeline_state():
    pack = ContextPack(examples=(Example("Revenue by month", "SELECT 1", 0.83),))
    state = {
        "intent": "data_query", "sql_attempts": 3, "asked": "and by region?", "assumptions": ["2025 means calendar"],
        "failures": [{"stage": "validate"}, {"stage": "execute"}, {"stage": "result"}], "context": pack,
        "rows": [[None]], "summary_verified": False,
        "sql": "SELECT SUM(x) OVER (PARTITION BY y) FROM (SELECT * FROM a JOIN b ON true) t",
    }
    s = Signals.from_state(state)
    assert (s.invalid_attempts, s.failed_runs, s.empty_retries) == (1, 1, 1)
    assert s.nearest_example == 0.83 and s.follow_up and s.assumptions == 1 and s.empty_result
    assert s.window_functions and s.subqueries == 1 and s.joins == 1 and s.summary_checked is False
    assert Signals.from_state({**state, "context": ContextPack()}).nearest_example == 0.0  # retrieval on, no match


def test_isotonic_calibration_never_decreases():
    cal = fit_isotonic([0.1, 0.2, 0.3, 0.4, 0.5, 0.6], [0, 1, 0, 1, 1, 1])
    assert cal.y == sorted(cal.y) and cal.y[0] == 0.0 and cal.y[-1] == 1.0
    assert cal(0.25) == 0.5 and cal(0.05) == 0.0 and cal(0.99) == 1.0


def test_auroc_and_calibration_error():
    assert auroc([0.9, 0.8, 0.3, 0.2], [1, 1, 0, 0]) == 1.0
    assert auroc([0.5, 0.5], [1, 0]) == 0.5
    assert auroc([0.9], [1]) is None
    assert calibration_error([0.95, 0.95, 0.05, 0.05], [1, 1, 0, 0]) == 0.05


def synthetic(n: int, seed: int) -> list[tuple[Signals, bool]]:
    """Answers where retries, empty results and assumptions make a wrong answer more likely, as in real runs."""
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        s = signals(attempts=rng.choice([1, 1, 1, 2, 3]), empty_result=rng.random() < 0.15,
                    assumptions=rng.choice([0, 0, 1, 2]), joins=rng.randint(0, 4), rows=rng.choice([1, 6, 40]))
        risk = 0.05 + 0.25 * (s.attempts - 1) + 0.4 * s.empty_result + 0.1 * s.assumptions
        out.append((s, rng.random() > risk))
    return out


def test_a_trained_model_separates_right_from_wrong_on_new_answers(tmp_path):
    dev, test = synthetic(200, 1), synthetic(200, 2)
    model = train([s for s, _ in dev], [c for _, c in dev], version="test")
    weights = dict(zip(model.features, model.weights, strict=True))
    assert weights["retries"] < 0 and weights["empty_result"] < 0

    y = [int(c) for _, c in test]
    assert auroc([model.score(s) for s, _ in test], y) > 0.7
    assessed = [model.assess(s) for s, _ in test]
    assert calibration_error([a.probability for a in assessed], y) < 0.15
    right = {label: [c for (s, c), a in zip(test, assessed, strict=True) if a.label == label] for label in ("high", "low")}
    assert sum(right["high"]) / len(right["high"]) > sum(right["low"]) / len(right["low"])

    save_model(model, tmp_path / "m.json")
    assert load_model(tmp_path / "m.json") == model
    assert load_model(tmp_path / "missing.json") is None


def test_a_loaded_model_labels_the_analysts_answers(db):
    model = ConfidenceModel(version="v", features=["retries"], weights=[-2.0], bias=2.0,
                            calibration=Isotonic(x=[0.5, 1.0], y=[0.4, 0.9]), high=0.85, low=0.6)
    answer = Analyst(FakeLLM(sql=[REVENUE_BY_REGION]), db, confidence=model).ask("Revenue by region")
    assert (answer.confidence.label, answer.confidence.probability, answer.confidence.model) == ("high", 0.9, "v")


def test_training_needs_both_outcomes():
    with pytest.raises(ValueError, match="both right and wrong"):
        train([signals()] * 40, [True] * 40, version="v")


def test_the_training_script_learns_from_dev_and_reports_on_held_out(tmp_path):
    def result(i, split, pair):
        s, correct = pair
        return {"id": i, "split": split, "correct": correct, "signals": s.model_dump()}

    results = [result(i, "dev", p) for i, p in enumerate(synthetic(120, 3))]
    results += [result(1000 + i, "test", p) for i, p in enumerate(synthetic(80, 4))]
    results.append({"id": 9999, "split": "dev", "correct": False, "signals": None})  # not a data answer
    run = tmp_path / "run.json"
    run.write_text(json.dumps({"summary": {"commit": "abc1234"}, "results": results}))
    out = tmp_path / "confidence.json"

    done = subprocess.run([sys.executable, str(ROOT / "eval" / "train_confidence.py"), str(run), "--out", str(out)],
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    assert "Trained on 120 dev answers" in done.stdout and "Held out: 80 answers" in done.stdout
    report = load_model(out).report
    assert report["held_out"]["answers"] == 80 and report["held_out"]["auroc"] > 0.6

    run.write_text(json.dumps({"summary": {"commit": "x"}, "results": results[:10]}))
    too_few = subprocess.run([sys.executable, str(ROOT / "eval" / "train_confidence.py"), str(run), "--dry-run"],
                             capture_output=True, text=True, timeout=60)
    assert too_few.returncode == 1 and "Need at least 30 dev answers" in too_few.stderr
