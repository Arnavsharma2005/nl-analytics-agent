"""Questions asked through /v1 and their answers, kept for a while so that a retried request is not answered
twice, and so that an answer can be rated or explained afterwards.

A message's id is the client's idempotency key. Sending the same key again replays the stored answer instead of
asking again; sending it while the first request is still running is refused. A message belongs to the visitor
session that asked it: other sessions can't read, rate or explain it.
"""

import json
import logging
import random
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.analyst import Answer

log = logging.getLogger(__name__)


class MessageV1(BaseModel):
    """One question in a conversation and its answer."""

    id: UUID
    conversation_id: UUID
    question: str
    answer: Answer
    suggestions: list[str] = Field(default_factory=list, description="Follow-up questions worth asking next.")
    created_at: datetime


class KeyReused(Exception):
    """The idempotency key was already used for a different question or by another session."""


class Messages:
    def __init__(self, engine: Engine, retention_days: int = 7, running_for_s: int = 120):
        self._engine = engine
        self._retention = timedelta(days=retention_days)
        # A message still unanswered after this long is taken to have failed, and may be asked again.
        self._running_for = timedelta(seconds=running_for_s)

    def begin(self, id: UUID, conversation_id: UUID, session: str | None,
              question: str) -> tuple[Literal["new", "running", "done"], MessageV1 | None]:
        """Claim `id` for a new question. Returns ("new", None) when the caller should answer it, ("running", None)
        while another request is answering it, or ("done", message) once it has been answered."""
        with self._engine.begin() as conn:
            if random.random() < 0.01:  # now and then, forget old messages
                conn.execute(text("DELETE FROM app.messages WHERE created_at < now() - :keep"),
                             {"keep": self._retention})
            claimed = conn.execute(text("""
                INSERT INTO app.messages (id, conversation_id, session, question)
                VALUES (:id, :conversation, :session, :question)
                ON CONFLICT (id) DO NOTHING RETURNING id
            """), {"id": id, "conversation": conversation_id, "session": session, "question": question}).first()
            if claimed:
                return "new", None
            row = conn.execute(text("""
                SELECT conversation_id, session, question, answer, created_at,
                       answer IS NULL AND created_at < now() - :running AS stale
                FROM app.messages WHERE id = :id FOR UPDATE
            """), {"id": id, "running": self._running_for}).first()
            if row is None:  # deleted between the two statements: treat as taken
                return "running", None
            if row.question != question or row.conversation_id != conversation_id or row.session != session:
                raise KeyReused()
            if row.answer is not None:
                return "done", self._message(id, row)
            if row.stale:
                conn.execute(text("UPDATE app.messages SET created_at = now() WHERE id = :id"), {"id": id})
                return "new", None
            return "running", None

    def finish(self, id: UUID, answer: Answer, suggestions: list[str]) -> MessageV1 | None:
        stored = {"answer": json.loads(answer.model_dump_json()), "suggestions": suggestions}
        with self._engine.begin() as conn:
            row = conn.execute(text("""
                UPDATE app.messages SET answer = CAST(:answer AS jsonb) WHERE id = :id
                RETURNING conversation_id, session, question, answer, created_at
            """), {"id": id, "answer": json.dumps(stored)}).first()
        return self._message(id, row) if row else None

    def abandon(self, id: UUID) -> None:
        """Forget a message that could not be answered, so the same key can be tried again."""
        with self._engine.begin() as conn:
            conn.execute(text("DELETE FROM app.messages WHERE id = :id AND answer IS NULL"), {"id": id})

    def get(self, id: UUID, session: str | None) -> MessageV1 | None:
        with self._engine.connect() as conn:
            row = conn.execute(text("""
                SELECT conversation_id, session, question, answer, created_at FROM app.messages
                WHERE id = :id AND answer IS NOT NULL AND session IS NOT DISTINCT FROM :session
            """), {"id": id, "session": session}).first()
        return self._message(id, row) if row else None

    def rate(self, id: UUID, session: str | None, rating: int, category: str | None, comment: str | None) -> bool:
        """Record (or replace) feedback on an answered message. False if there is no such message."""
        with self._engine.begin() as conn:
            done = conn.execute(text("""
                INSERT INTO app.feedback (message_id, rating, category, comment)
                SELECT id, :rating, :category, :comment FROM app.messages
                WHERE id = :id AND answer IS NOT NULL AND session IS NOT DISTINCT FROM :session
                ON CONFLICT (message_id) DO UPDATE
                SET rating = EXCLUDED.rating, category = EXCLUDED.category, comment = EXCLUDED.comment,
                    created_at = now()
                RETURNING message_id
            """), {"id": id, "session": session, "rating": rating, "category": category, "comment": comment}).first()
        return done is not None

    def explanation(self, id: UUID) -> list[str] | None:
        with self._engine.connect() as conn:
            return conn.execute(text("SELECT explanation FROM app.messages WHERE id = :id"), {"id": id}).scalar()

    def save_explanation(self, id: UUID, steps: list[str]) -> None:
        with self._engine.begin() as conn:
            conn.execute(text("UPDATE app.messages SET explanation = CAST(:steps AS jsonb) WHERE id = :id"),
                         {"id": id, "steps": json.dumps(steps)})

    @staticmethod
    def _message(id: UUID, row) -> MessageV1:
        return MessageV1(id=id, conversation_id=row.conversation_id, question=row.question,
                         answer=row.answer["answer"], suggestions=row.answer["suggestions"], created_at=row.created_at)
