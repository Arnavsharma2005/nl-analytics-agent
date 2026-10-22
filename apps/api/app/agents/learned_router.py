"""A router learned from labelled questions, asked before the model: when it is sure, the model call is skipped.

Each question becomes a bag of hashed features (words, word pairs and the three-letter pieces of words, so "revenu"
and "revenues" still share most of theirs), weighted and scaled to unit length, and a multinomial logistic
regression over them gives the probability of each intent. Only an answer it gives at least THRESHOLD probability,
for an intent that is on, is used; anything else goes to the model router as before.

The examples are kept in `semantic/router.yml` (written for the demo store; another database brings its own or
goes without), expanded from templates, and trained by `eval/train_router.py` into `semantic/router.json`. Training
holds a fifth of the examples out and refuses to write a model that is wrong on more than MAX_CONFIDENT_ERROR of
the held-out questions it is sure about.

Pure Python, like app/analysis/forecast.py: a few hundred short questions train in a second, and a model this size
answers in well under a millisecond, so neither numpy nor an embedding call would earn its cost here.
"""

import hashlib
import itertools
import json
import math
import random
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

THRESHOLD = 0.9
MAX_CONFIDENT_ERROR = 0.02
BUCKETS = 1 << 16
WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


def _bucket(feature: str) -> int:
    return int.from_bytes(hashlib.blake2b(feature.encode(), digest_size=4).digest(), "big") % BUCKETS


def features(text: str) -> dict[int, float]:
    """The question as hashed features of unit length."""
    words = WORD.findall(text.lower())
    names = [f"w:{w}" for w in words] + [f"b:{a} {b}" for a, b in itertools.pairwise(words)]
    for w in words:
        padded = f"<{w}>"
        names += [f"c:{padded[i:i + 3]}" for i in range(len(padded) - 2)]
    counts: dict[int, float] = {}
    for name in names:
        k = _bucket(name)
        counts[k] = counts.get(k, 0.0) + 1.0
    weighted = {k: 1.0 + math.log(v) for k, v in counts.items()}
    norm = math.sqrt(sum(v * v for v in weighted.values())) or 1.0
    return {k: v / norm for k, v in weighted.items()}


@dataclass(frozen=True)
class Prediction:
    intent: str
    probability: float


class LearnedRouter:
    def __init__(self, intents: Sequence[str], weights: list[dict[int, float]], bias: list[float]):
        self.intents = list(intents)
        self._weights = weights
        self._bias = bias

    def probabilities(self, question: str) -> dict[str, float]:
        x = features(question)
        scores = [b + sum(w.get(k, 0.0) * v for k, v in x.items()) for w, b in zip(self._weights, self._bias,
                                                                                   strict=True)]
        top = max(scores)
        exp = [math.exp(s - top) for s in scores]
        total = sum(exp)
        return {intent: e / total for intent, e in zip(self.intents, exp, strict=True)}

    def predict(self, question: str) -> Prediction:
        p = self.probabilities(question)
        intent = max(p, key=p.__getitem__)
        return Prediction(intent, p[intent])

    def decide(self, question: str, allowed: Iterable[str]) -> Prediction | None:
        """The intent when it is sure of one that is on; otherwise None, and the model decides."""
        guess = self.predict(question)
        return guess if guess.probability >= THRESHOLD and guess.intent in set(allowed) else None

    # --- Training and storage ------------------------------------------------------------------------------------

    @classmethod
    def train(cls, examples: Sequence[tuple[str, str]], epochs: int = 30, rate: float = 0.5, l2: float = 1e-4,
              seed: int = 7) -> "LearnedRouter":
        """Multinomial logistic regression by stochastic gradient descent, shuffled the same way every time."""
        intents = sorted({label for _, label in examples})
        index = {label: i for i, label in enumerate(intents)}
        data = [(features(q), index[label]) for q, label in examples]
        weights: list[dict[int, float]] = [{} for _ in intents]
        bias = [0.0] * len(intents)
        order = list(range(len(data)))
        rng = random.Random(seed)
        for epoch in range(epochs):
            rng.shuffle(order)
            step = rate / (1 + epoch * 0.2)
            for n in order:
                x, y = data[n]
                scores = [b + sum(w.get(k, 0.0) * v for k, v in x.items()) for w, b in zip(weights, bias, strict=True)]
                top = max(scores)
                exp = [math.exp(s - top) for s in scores]
                total = sum(exp)
                for c, e in enumerate(exp):
                    grad = e / total - (1.0 if c == y else 0.0)
                    w = weights[c]
                    for k, v in x.items():
                        w[k] = w.get(k, 0.0) * (1 - step * l2) - step * grad * v
                    bias[c] -= step * grad
        return cls(intents, weights, bias)

    def save(self, path: Path) -> None:
        weights = [{str(k): round(v, 4) for k, v in sorted(w.items()) if abs(v) >= 1e-3} for w in self._weights]
        path.write_text(json.dumps({"version": 1, "buckets": BUCKETS, "threshold": THRESHOLD, "intents": self.intents,
                                    "bias": [round(b, 4) for b in self._bias], "weights": weights},
                                   separators=(",", ":")) + "\n")

    @classmethod
    def load(cls, path: Path) -> "LearnedRouter | None":
        if not path.is_file():
            return None
        data = json.loads(path.read_text())
        if data.get("version") != 1 or data.get("buckets") != BUCKETS:
            raise ValueError(f"{path} was trained by another version of the learned router; train it again")
        return cls(data["intents"], [{int(k): v for k, v in w.items()} for w in data["weights"]], data["bias"])


# --- Examples ----------------------------------------------------------------------------------------------------


def examples(path: Path) -> list[tuple[str, str]]:
    """The labelled questions in a router.yml: each intent's listed questions, then its templates filled in with
    slot values (`{metric}` takes values from `slots.metric`), without repeats. A template with more combinations
    than `per_template` is filled with that many, chosen the same way every time, so no intent swamps the rest."""
    spec = yaml.safe_load(path.read_text())
    slots: dict[str, list[str]] = spec.get("slots", {})
    cap = spec.get("per_template", 25)
    out: dict[tuple[str, str], None] = {}
    for intent, body in spec["intents"].items():
        for q in body.get("questions", []):
            out[(q.strip(), intent)] = None
        for template in body.get("templates", []):
            names = re.findall(r"\{(\w+)\}", template)
            combos = list(itertools.product(*(slots[n] for n in names)))
            if len(combos) > cap:
                combos = random.Random(template).sample(combos, cap)
            for values in combos:
                filled = template.format(**dict(zip(names, values, strict=True)))
                out[(filled[0].upper() + filled[1:], intent)] = None
    return list(out)


def held_out(question: str) -> bool:
    """A fixed fifth of the questions, by a hash of their text, kept out of training to measure the model."""
    return hashlib.blake2b(question.lower().encode(), digest_size=1).digest()[0] % 5 == 0


@dataclass(frozen=True)
class Report:
    examples: int
    held_out: int
    accuracy: float  # on every held-out question
    coverage: float  # the share of held-out questions it is sure about
    confident_error: float  # how often it is wrong when sure

    @property
    def good_enough(self) -> bool:
        return self.confident_error <= MAX_CONFIDENT_ERROR


def evaluate(model: LearnedRouter, test: Sequence[tuple[str, str]], n_examples: int) -> Report:
    guesses = [(model.predict(q), label) for q, label in test]
    sure = [(g, label) for g, label in guesses if g.probability >= THRESHOLD]
    return Report(examples=n_examples, held_out=len(test),
                  accuracy=sum(g.intent == label for g, label in guesses) / max(len(test), 1),
                  coverage=len(sure) / max(len(test), 1),
                  confident_error=sum(g.intent != label for g, label in sure) / max(len(sure), 1))
