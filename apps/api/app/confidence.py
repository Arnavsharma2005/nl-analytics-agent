"""How likely an answer is to be right, from what the pipeline already knows about how it got there.

Every data answer carries its `Signals`: whether a verified query answered it, how close the nearest verified
example was, how many tries the SQL took and why the failed ones failed, whether the result was empty, how many
readings of an ambiguous question were assumed, and how involved the SQL is. They cost nothing to collect.

A `ConfidenceModel` turns them into a probability and a High, Medium or Low label. It is a logistic regression
whose output is then calibrated (isotonic regression), trained by eval/train_confidence.py on the dev split of a
real eval run and scored on the held-out split. Until a model has been trained (models/confidence.json), answers
carry their signals and no label; the label is also not shown until its calibration has been checked on live
answers (shadow mode). Pure Python: the model is a few dozen numbers, not worth numpy's memory on the web tier.
"""

import json
import math
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.checks import looks_empty

MODEL_PATH = Path(__file__).resolve().parents[1] / "models" / "confidence.json"


class Signals(BaseModel):
    """What the pipeline knows about how an answer was reached."""

    verified: bool = Field(description="A verified query answered the question as written.")
    nearest_example: float | None = Field(
        description="Similarity (0 to 1) of the closest verified example the SQL agent was shown; None when "
                    "context retrieval is off.")
    attempts: int = Field(description="Queries the SQL agent wrote.")
    invalid_attempts: int = Field(description="Queries refused by the guard or the planner.")
    failed_runs: int = Field(description="Queries that failed when run.")
    empty_retries: int = Field(description="Queries sent back because they found nothing.")
    empty_result: bool = Field(description="No rows, or nothing but NULLs.")
    rows: int
    assumptions: int = Field(description="Readings of an ambiguous question the SQL agent chose.")
    follow_up: bool = Field(description="The question was rewritten using earlier turns of its conversation.")
    summary_checked: bool | None = Field(description="Every figure in the summary was found in the result "
                                                     "(None: not checked).")
    joins: int
    window_functions: bool
    subqueries: int

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> "Signals":
        failures = state.get("failures") or []
        stages = [f["stage"] for f in failures]
        pack = state.get("context")
        rows = state.get("rows") or []
        sql = (state.get("sql") or "").lower()
        return cls(
            verified=bool(state.get("verified_question")),
            nearest_example=None if pack is None else (pack.examples[0].similarity if pack.examples else 0.0),
            attempts=state.get("sql_attempts", 0),
            invalid_attempts=stages.count("validate"),
            failed_runs=stages.count("execute"),
            empty_retries=stages.count("result"),
            empty_result=looks_empty(rows),
            rows=len(rows),
            assumptions=len(state.get("assumptions") or []),
            follow_up=bool(state.get("asked")),
            summary_checked=state.get("summary_verified"),
            joins=len(re.findall(r"\bjoin\b", sql)),
            window_functions=bool(re.search(r"\bover\s*\(", sql)),
            subqueries=max(len(re.findall(r"\bselect\b", sql)) - 1, 0),
        )

    def features(self) -> dict[str, float]:
        """The signals as the model's numeric inputs."""
        return {
            "verified": float(self.verified),
            "has_examples": float(self.nearest_example is not None),
            "nearest_example": self.nearest_example or 0.0,
            "retries": float(max(self.attempts - 1, 0)),
            "invalid_attempts": float(self.invalid_attempts),
            "failed_runs": float(self.failed_runs),
            "empty_retries": float(self.empty_retries),
            "empty_result": float(self.empty_result),
            "single_row": float(self.rows == 1),
            "log_rows": math.log1p(self.rows),
            "assumptions": float(self.assumptions),
            "follow_up": float(self.follow_up),
            "summary_unsupported": float(self.summary_checked is False),
            "joins": float(self.joins),
            "window_functions": float(self.window_functions),
            "subqueries": float(self.subqueries),
        }


Label = Literal["high", "medium", "low"]


class Confidence(BaseModel):
    """An answer's signals and, once a model is trained, how likely it is to be right."""

    signals: Signals
    probability: float | None = Field(default=None, description="Calibrated chance the answer is right.")
    label: Label | None = None
    model: str | None = Field(default=None, description="Which trained model scored it.")


class Isotonic(BaseModel):
    """A non-decreasing step function from raw scores to calibrated probabilities."""

    x: list[float]  # upper edge of each step, ascending
    y: list[float]  # probability on each step, non-decreasing

    def __call__(self, score: float) -> float:
        for edge, value in zip(self.x, self.y, strict=True):
            if score <= edge:
                return value
        return self.y[-1]


class ConfidenceModel(BaseModel):
    version: str
    features: list[str]
    weights: list[float]
    bias: float
    calibration: Isotonic
    high: float = Field(description="At or above this probability an answer is High confidence.")
    low: float = Field(description="Below this probability an answer is Low confidence.")
    report: dict[str, Any] = Field(default_factory=dict, description="How it was trained and how well it did.")

    def score(self, signals: Signals) -> float:
        """The raw logistic score, before calibration."""
        values = signals.features()
        z = self.bias + sum(w * values[name] for name, w in zip(self.features, self.weights, strict=True))
        return _sigmoid(z)

    def assess(self, signals: Signals) -> Confidence:
        p = round(self.calibration(self.score(signals)), 3)
        label: Label = "high" if p >= self.high else "low" if p < self.low else "medium"
        return Confidence(signals=signals, probability=p, label=label, model=self.version)


def load_model(path: Path = MODEL_PATH) -> ConfidenceModel | None:
    """The trained model, or None before one has been trained."""
    if not path.exists():
        return None
    return ConfidenceModel.model_validate_json(path.read_text(encoding="utf-8"))


def assess(state: dict[str, Any], model: ConfidenceModel | None) -> Confidence | None:
    """An answer's confidence; None for anything but an answered data question."""
    if state.get("intent") != "data_query" or state.get("error"):
        return None
    signals = Signals.from_state(state)
    return model.assess(signals) if model else Confidence(signals=signals)


# --- Training (eval/train_confidence.py) ---------------------------------------------------------------------


def _sigmoid(z: float) -> float:
    return 1 / (1 + math.exp(-z)) if z >= 0 else math.exp(z) / (1 + math.exp(z))


def fit_logistic(x: Sequence[Sequence[float]], y: Sequence[int], l2: float = 1.0, steps: int = 3000,
                 rate: float = 0.5) -> tuple[list[float], float]:
    """L2-regularised logistic regression by gradient descent on standardised inputs; returns weights and bias
    for the raw inputs."""
    n, k = len(x), len(x[0])
    mean = [sum(row[j] for row in x) / n for j in range(k)]
    std = [math.sqrt(sum((row[j] - mean[j]) ** 2 for row in x) / n) or 1.0 for j in range(k)]
    z = [[(row[j] - mean[j]) / std[j] for j in range(k)] for row in x]
    w, b = [0.0] * k, math.log((sum(y) + 0.5) / (n - sum(y) + 0.5))
    for _ in range(steps):
        errors = [_sigmoid(b + sum(wj * zj for wj, zj in zip(w, row, strict=True))) - t
                  for row, t in zip(z, y, strict=True)]
        grad = [sum(e * row[j] for e, row in zip(errors, z, strict=True)) / n + l2 * w[j] / n for j in range(k)]
        w = [wj - rate * g for wj, g in zip(w, grad, strict=True)]
        b -= rate * sum(errors) / n
    weights = [w[j] / std[j] for j in range(k)]
    return weights, b - sum(weights[j] * mean[j] for j in range(k))


def fit_isotonic(scores: Sequence[float], y: Sequence[int]) -> Isotonic:
    """Pool-adjacent-violators: the non-decreasing step function closest to the outcomes, sorted by score."""
    blocks: list[list[float]] = []  # [sum of outcomes, count, highest score]
    for s, t in sorted(zip(scores, y, strict=True)):
        blocks.append([float(t), 1.0, s])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] >= blocks[-1][0] / blocks[-1][1]:
            total, count, top = blocks.pop()
            blocks[-1][0] += total
            blocks[-1][1] += count
            blocks[-1][2] = top
    return Isotonic(x=[round(b[2], 6) for b in blocks], y=[round(b[0] / b[1], 4) for b in blocks])


def auroc(scores: Sequence[float], y: Sequence[int]) -> float | None:
    """Chance a right answer scores above a wrong one (ties count half); None without both kinds."""
    right = [s for s, t in zip(scores, y, strict=True) if t]
    wrong = [s for s, t in zip(scores, y, strict=True) if not t]
    if not right or not wrong:
        return None
    wins = sum(1.0 if r > w else 0.5 if r == w else 0.0 for r in right for w in wrong)
    return round(wins / (len(right) * len(wrong)), 3)


def calibration_error(probabilities: Sequence[float], y: Sequence[int], bins: int = 10) -> float:
    """Expected calibration error: how far stated probabilities are from how often answers were right."""
    groups: dict[int, list[tuple[float, int]]] = {}
    for p, t in zip(probabilities, y, strict=True):
        groups.setdefault(min(int(p * bins), bins - 1), []).append((p, t))
    n = len(probabilities)
    return round(sum(abs(sum(p for p, _ in g) / len(g) - sum(t for _, t in g) / len(g)) * len(g) / n
                     for g in groups.values()), 3)


def train(train_signals: Sequence[Signals], train_correct: Sequence[bool], *, version: str, folds: int = 5,
          high: float = 0.85, low: float = 0.6) -> ConfidenceModel:
    """Fit the regression on every training answer, and its calibration on out-of-fold scores so that the
    calibration does not learn the regression's own fit."""
    names = list(train_signals[0].features())
    x = [[s.features()[name] for name in names] for s in train_signals]
    y = [int(c) for c in train_correct]
    if len(set(y)) < 2:
        raise ValueError("training needs both right and wrong answers")
    out_of_fold = [0.0] * len(x)
    for fold in range(folds):
        held = [i for i in range(len(x)) if i % folds == fold]
        kept = [i for i in range(len(x)) if i % folds != fold]
        if len({y[i] for i in kept}) < 2:  # a fold without both outcomes can't be fitted; score it neutrally
            for i in held:
                out_of_fold[i] = sum(y) / len(y)
            continue
        w, b = fit_logistic([x[i] for i in kept], [y[i] for i in kept])
        for i in held:
            out_of_fold[i] = _sigmoid(b + sum(wj * xj for wj, xj in zip(w, x[i], strict=True)))
    weights, bias = fit_logistic(x, y)
    return ConfidenceModel(version=version, features=names, weights=[round(w, 6) for w in weights],
                           bias=round(bias, 6), calibration=fit_isotonic(out_of_fold, y), high=high, low=low)


def save_model(model: ConfidenceModel, path: Path = MODEL_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(model.model_dump(), indent=1) + "\n", encoding="utf-8")
