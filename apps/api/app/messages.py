"""Questions asked through /v1 and their answers, kept for a while so that a retried request is not answered
twice, and so that an answer can be rated or explained afterwards.

A message's id is the client's idempotency key. Sending the same key again replays the stored answer instead of
asking again; sending it while the first request is still running is refused.

A message belongs to its owner: the signed-in user who asked it ("user:<id>"), or else the anonymous session. No one
else can read, rate or explain it, or see it as history. A user's messages are kept longer than a session's.
"""

import json
import logging
import random
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.agents.followup import Turn
from app.analyst import Answer

log = logging.getLogger(__name__)


class MessageV1(BaseModel):
    """One question in a conversation and its answer."""

    # Responses always carry every field, so the OpenAPI schema marks them required for generated clients.
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    id: UUID
    conversation_id: UUID
    dataset_id: UUID | None = Field(default=None, description="The uploaded dataset it asked about; none for the demo data.")
    question: str
    answer: Answer
    suggestions: list[str] = Field(default_factory=list, description="Follow-up questions worth asking next.")
    created_at: datetime


class KeyReused(Exception):
    """The idempotency key was already used for a different question or by someone else."""


class ConversationV1(BaseModel):
    """A conversation in someone's history."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    id: UUID
    title: str = Field(description="Its first question.")
    dataset_id: UUID | None = Field(default=None, description="The uploaded dataset it is about; none for the demo data.")
    messages: int
    updated_at: datetime


class Messages:
    def __init__(self, engine: Engine, retention_days: int = 7, running_for_s: int = 120, user_retention_days: int = 90):
        self._engine = engine
        self._retention = timedelta(days=retention_days)
        self._user_retention = timedelta(days=user_retention_days)
        # A message still unanswered after this long is taken to have failed, and may be asked again.
        self._running_for = timedelta(seconds=running_for_s)

    def begin(self, id: UUID, conversation_id: UUID, owner: str | None, question: str,
              dataset_id: UUID | None = None) -> tuple[Literal["new", "running", "done"], MessageV1 | None]:
        """Claim `id` for a new question. Returns ("new", None) when the caller should answer it, ("running", None)
        while another request is answering it, or ("done", message) once it has been answered."""
        with self._engine.begin() as conn:
            if random.random() < 0.01:  # now and then, forget old messages
                conn.execute(text("""
                    DELETE FROM app.messages
                    WHERE created_at < now() - CASE WHEN owner LIKE 'user:%' THEN :keep_users ELSE :keep END
                """), {"keep": self._retention, "keep_users": self._user_retention})
            claimed = conn.execute(text("""
                INSERT INTO app.messages (id, conversation_id, owner, question, dataset_id)
                VALUES (:id, :conversation, :owner, :question, :dataset)
                ON CONFLICT (id) DO NOTHING RETURNING id
            """), {"id": id, "conversation": conversation_id, "owner": owner, "question": question,
                   "dataset": dataset_id}).first()
            if claimed:
                return "new", None
            row = conn.execute(text("""
                SELECT conversation_id, dataset_id, owner, question, answer, created_at,
                       answer IS NULL AND created_at < now() - :running AS stale
                FROM app.messages WHERE id = :id FOR UPDATE
            """), {"id": id, "running": self._running_for}).first()
            if row is None:  # deleted between the two statements: treat as taken
                return "running", None
            if (row.question, row.conversation_id, row.owner, row.dataset_id) != (question, conversation_id, owner,
                                                                                  dataset_id):
                raise KeyReused()
            if row.answer is not None:
                return "done", self._message(id, row)
            if row.stale:
                conn.execute(text("UPDATE app.messages SET created_at = now() WHERE id = :id"), {"id": id})
                return "new", None
            return "running", None

    def history(self, conversation_id: UUID, owner: str | None, before: UUID, limit: int = 3,
                dataset_id: UUID | None = None) -> list[Turn]:
        """The last answered turns of a conversation asked before message `before`, oldest first, each as it was
        understood. Only the owner's own messages count, so a guessed conversation id reveals nothing, and only
        those about the same data (`dataset_id`, or the demo's when None), since SQL about other tables would
        mislead a follow-up."""
        with self._engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT answer FROM app.messages
                WHERE conversation_id = :conversation AND owner IS NOT DISTINCT FROM :owner
                  AND dataset_id IS NOT DISTINCT FROM :dataset AND answer IS NOT NULL AND id <> :before
                  AND created_at <= (SELECT coalesce(max(created_at), now()) FROM app.messages WHERE id = :before)
                ORDER BY created_at DESC LIMIT :limit
            """), {"conversation": conversation_id, "owner": owner, "before": before, "limit": limit,
                                    "dataset": dataset_id}).all()
        return [Answer.model_validate(row.answer["answer"]).as_turn() for row in reversed(rows)]

    def finish(self, id: UUID, answer: Answer, suggestions: list[str]) -> MessageV1 | None:
        stored = {"answer": json.loads(answer.model_dump_json()), "suggestions": suggestions}
        with self._engine.begin() as conn:
            row = conn.execute(text("""
                UPDATE app.messages SET answer = CAST(:answer AS jsonb) WHERE id = :id
                RETURNING conversation_id, dataset_id, owner, question, answer, created_at
            """), {"id": id, "answer": json.dumps(stored)}).first()
        return self._message(id, row) if row else None

    def adopt(self, session: str, owner: str) -> int:
        """Give a session's messages to the user who just signed in from it, so the conversation carries on."""
        with self._engine.begin() as conn:
            return conn.execute(text("UPDATE app.messages SET owner = :owner WHERE owner = :session"),
                                {"owner": owner, "session": session}).rowcount

    def abandon(self, id: UUID) -> None:
        """Forget a message that could not be answered, so the same key can be tried again."""
        with self._engine.begin() as conn:
            conn.execute(text("DELETE FROM app.messages WHERE id = :id AND answer IS NULL"), {"id": id})

    def get(self, id: UUID, owner: str | None) -> MessageV1 | None:
        with self._engine.connect() as conn:
            row = conn.execute(text("""
                SELECT conversation_id, dataset_id, owner, question, answer, created_at FROM app.messages
                WHERE id = :id AND answer IS NOT NULL AND owner IS NOT DISTINCT FROM :owner
            """), {"id": id, "owner": owner}).first()
        return self._message(id, row) if row else None

    def conversations(self, owner: str | None, limit: int = 50) -> list[ConversationV1]:
        """The owner's conversations with at least one answer, most recent first. Nothing without an owner."""
        if not owner:
            return []
        with self._engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT conversation_id AS id, (array_agg(question ORDER BY created_at))[1] AS title,
                       (array_agg(dataset_id ORDER BY created_at))[1] AS dataset_id,
                       COUNT(*) AS messages, max(created_at) AS updated_at
                FROM app.messages WHERE owner = :owner AND answer IS NOT NULL
                GROUP BY conversation_id ORDER BY updated_at DESC LIMIT :limit
            """), {"owner": owner, "limit": limit}).all()
        return [ConversationV1.model_validate(row._asdict()) for row in rows]

    def conversation(self, conversation_id: UUID, owner: str | None) -> list[MessageV1]:
        """The owner's answered messages in a conversation, oldest first."""
        if not owner:
            return []
        with self._engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT id, conversation_id, dataset_id, owner, question, answer, created_at FROM app.messages
                WHERE conversation_id = :conversation AND owner = :owner AND answer IS NOT NULL
                ORDER BY created_at
            """), {"conversation": conversation_id, "owner": owner}).all()
        return [self._message(row.id, row) for row in rows]

    def rate(self, id: UUID, owner: str | None, rating: int, category: str | None, comment: str | None) -> bool:
        """Record (or replace) feedback on an answered message. False if there is no such message."""
        with self._engine.begin() as conn:
            done = conn.execute(text("""
                INSERT INTO app.feedback (message_id, rating, category, comment)
                SELECT id, :rating, :category, :comment FROM app.messages
                WHERE id = :id AND answer IS NOT NULL AND owner IS NOT DISTINCT FROM :owner
                ON CONFLICT (message_id) DO UPDATE
                SET rating = EXCLUDED.rating, category = EXCLUDED.category, comment = EXCLUDED.comment,
                    created_at = now()
                RETURNING message_id
            """), {"id": id, "owner": owner, "rating": rating, "category": category, "comment": comment}).first()
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
        return MessageV1(id=id, conversation_id=row.conversation_id, dataset_id=row.dataset_id, question=row.question,
                         answer=row.answer["answer"], suggestions=row.answer["suggestions"], created_at=row.created_at)
