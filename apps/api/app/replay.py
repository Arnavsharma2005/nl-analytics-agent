"""Record a model's answers once, then replay them so the pipeline can be re-run without keys or rate limits.

A cassette is a JSON file mapping each prompt to the answers the model gave it, in order. `RecordingLLM` wraps a
real model and writes one; `ReplayLLM` serves from one. Replaying the eval set this way shows whether a code change
alters any outcome while the model's behaviour is held fixed. Calls on which every provider failed are recorded too,
and replay raises the same `AllProvidersFailed`. A prompt the cassette has never seen raises `CassetteMiss`: the
change altered what the model is asked, so the cassette needs recording again.
"""

import hashlib
import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from app.llm import LLM, AllProvidersFailed, CallLogging, Tier

T = TypeVar("T", bound=BaseModel)


class CassetteMiss(KeyError):
    def __init__(self, kind: str, user: str):
        super().__init__(f"no recorded {kind} answer for this prompt (record the cassette again): {user[:200]!r}")


def _kind(name: str, tier: Tier) -> str:
    # Answers from the fast and strong models are kept apart, so changing which steps use which tier is a miss.
    return name if tier == "strong" else f"{name}@fast"


def _key(kind: str, system: str, user: str) -> str:
    return f"{kind}:" + hashlib.sha256(f"{system}\0{user}".encode()).hexdigest()[:24]


class RecordingLLM:
    """Passes every call to `inner` and keeps its answers; `save` writes them as a cassette."""

    def __init__(self, inner: LLM):
        self._inner = inner
        self._entries: dict[str, dict] = {}
        self._lock = threading.Lock()

    def structured(self, output: type[T], system: str, user: str, *, tier: Tier = "strong") -> T:
        kind = _kind(output.__name__, tier)
        try:
            result = self._inner.structured(output, system, user, tier=tier)
        except AllProvidersFailed as e:
            self._keep(kind, system, user, {"failed": e.errors})
            raise
        self._keep(kind, system, user, {"ok": result.model_dump(mode="json")})
        return result

    def text(self, system: str, user: str, *, tier: Tier = "strong") -> str:
        kind = _kind("text", tier)
        try:
            result = self._inner.text(system, user, tier=tier)
        except AllProvidersFailed as e:
            self._keep(kind, system, user, {"failed": e.errors})
            raise
        self._keep(kind, system, user, {"ok": result})
        return result

    def stream_text(self, system: str, user: str, *, tier: Tier = "strong") -> Iterator[str]:
        # Kept as one text answer, so a streamed reply and a plain one replay the same way.
        kind, parts = _kind("text", tier), []
        try:
            for piece in self._inner.stream_text(system, user, tier=tier):
                parts.append(piece)
                yield piece
        except AllProvidersFailed as e:
            self._keep(kind, system, user, {"failed": e.errors})
            raise
        self._keep(kind, system, user, {"ok": "".join(parts).strip()})

    def start_log(self) -> None:
        if isinstance(self._inner, CallLogging):
            self._inner.start_log()

    def take_log(self) -> list[dict]:
        return self._inner.take_log() if isinstance(self._inner, CallLogging) else []

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            data = dict(sorted(self._entries.items()))
        path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")

    def _keep(self, kind: str, system: str, user: str, answer) -> None:
        with self._lock:
            entry = self._entries.setdefault(_key(kind, system, user), {"prompt": user[-300:], "answers": []})
            entry["answers"].append(answer)


class ReplayLLM:
    """Serves the answers in a cassette. A prompt asked more often than recorded gets its last answer again."""

    def __init__(self, path: Path):
        self._entries: dict[str, dict] = json.loads(path.read_text())
        self._served: dict[str, int] = {}
        self._lock = threading.Lock()

    def structured(self, output: type[T], system: str, user: str, *, tier: Tier = "strong") -> T:
        return output.model_validate(self._next(_kind(output.__name__, tier), system, user))

    def text(self, system: str, user: str, *, tier: Tier = "strong") -> str:
        return self._next(_kind("text", tier), system, user)

    def stream_text(self, system: str, user: str, *, tier: Tier = "strong") -> Iterator[str]:
        yield self.text(system, user, tier=tier)

    def _next(self, kind: str, system: str, user: str):
        key = _key(kind, system, user)
        if key not in self._entries:
            raise CassetteMiss(kind, user)
        answers = self._entries[key]["answers"]
        with self._lock:
            n = self._served.get(key, 0)
            self._served[key] = n + 1
        answer = answers[min(n, len(answers) - 1)]
        if "failed" in answer:
            raise AllProvidersFailed(answer["failed"])
        return answer["ok"]
