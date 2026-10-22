"""Answers to questions asked before, kept for a while so a repeat costs no model calls.

The key is the question with case, spacing and closing punctuation normalised, plus a version string that changes
with every deploy (the git commit), so a change to prompts or code never serves a stale answer. Only complete
answers are kept: no errors, no answers that ran out of model providers part-way.
"""

import hashlib
import logging
import re
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.analyst import Answer

log = logging.getLogger(__name__)


def normalise(question: str) -> str:
    return re.sub(r"\s+", " ", question).strip().rstrip("?.! ").lower()


class AnswerCache:
    def __init__(self, engine: Engine, version: str, ttl_s: int):
        self._engine = engine
        self._version = version
        self._ttl = timedelta(seconds=ttl_s)

    def _key(self, question: str) -> str:
        return hashlib.sha256(f"{self._version}\0{normalise(question)}".encode()).hexdigest()

    def get(self, question: str) -> Answer | None:
        try:
            with self._engine.begin() as conn:
                row = conn.execute(text("""
                    UPDATE app.answer_cache SET hits = hits + 1
                    WHERE key = :key AND created_at > now() - :ttl
                    RETURNING answer
                """), {"key": self._key(question), "ttl": self._ttl}).first()
        except Exception:  # noqa: BLE001 - a cache that can't be read is a cache miss
            log.exception("answer cache unavailable")
            return None
        if row is None:
            return None
        return Answer.model_validate(row[0]).model_copy(update={"question": question, "cached": True})

    def put(self, question: str, answer: Answer) -> None:
        if not cacheable(answer):
            return
        stored = answer.model_dump_json(exclude={"timings", "llm_calls", "cached"})
        try:
            with self._engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO app.answer_cache (key, answer) VALUES (:key, CAST(:answer AS jsonb))
                    ON CONFLICT (key) DO UPDATE SET answer = EXCLUDED.answer, created_at = now(), hits = 0
                """), {"key": self._key(question), "answer": stored})
        except Exception:  # noqa: BLE001 - failing to save only costs a future cache hit
            log.exception("answer cache unavailable")


def cacheable(answer: Answer) -> bool:
    if answer.error or answer.intent not in ("data_query", "schema_question"):
        return False
    # A data answer whose summary is missing ran out of providers part-way; ask again next time.
    return answer.intent != "data_query" or answer.summary is not None
