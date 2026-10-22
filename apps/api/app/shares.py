"""Share links: a read-only snapshot of one answer that anyone with the link can open.

A share copies the answer as it was (question, summary, chart, table and SQL) and never runs anything again, so
opening a link costs nothing and shows exactly what its owner saw. Only the owner of a message can share it, and
sharing the same message twice gives the same link. The link's id is a random token, so links can't be guessed or
listed. Shares are kept for 90 days, or until their owner deletes them.
"""

import json
import secrets
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.analyst import Answer
from app.messages import MessageV1


class ShareV1(BaseModel):
    """A shared answer, as it was when it was shared."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    id: str = Field(description="The token in the share link: /s/{id}.")
    question: str
    about: str | None = Field(description="The uploaded file it was about; none for the demo's sales data.")
    answer: Answer
    asked_at: datetime
    expires_at: datetime


def _snapshot(message: MessageV1, about: str | None) -> dict:
    # Only what the reader sees: which models answered and the failed tries are the owner's business.
    answer = message.answer.model_copy(update={"llm_calls": [], "failures": [], "timings": {}})
    return {"question": message.question, "about": about, "answer": answer.model_dump(mode="json"),
            "asked_at": message.created_at.isoformat()}


class Shares:
    def __init__(self, engine: Engine, keep_days: int = 90):
        self._engine = engine
        self._keep = timedelta(days=keep_days)

    def share(self, owner: str, message: MessageV1, about: str | None = None) -> ShareV1:
        """A link to `message`, which `owner` must own (the caller checks). The same link if it was shared before."""
        with self._engine.begin() as conn:
            conn.execute(text("DELETE FROM app.shares WHERE expires_at < now()"))
            row = conn.execute(text("""
                INSERT INTO app.shares (id, owner, message_id, snapshot, expires_at)
                VALUES (:id, :owner, :message, CAST(:snapshot AS jsonb), now() + :keep)
                ON CONFLICT (message_id) DO UPDATE SET expires_at = EXCLUDED.expires_at
                RETURNING id, snapshot, expires_at
            """), {"id": secrets.token_urlsafe(12), "owner": owner, "message": message.id,
                   "snapshot": json.dumps(_snapshot(message, about)), "keep": self._keep}).one()
        return self._share(row)

    def get(self, id: str) -> ShareV1 | None:
        with self._engine.connect() as conn:
            row = conn.execute(text("""
                SELECT id, snapshot, expires_at FROM app.shares WHERE id = :id AND expires_at > now()
            """), {"id": id}).first()
        return self._share(row) if row else None

    def delete(self, id: str, owner: str | None) -> bool:
        if not owner:
            return False
        with self._engine.begin() as conn:
            return conn.execute(text("DELETE FROM app.shares WHERE id = :id AND owner = :owner"),
                                {"id": id, "owner": owner}).rowcount > 0

    def adopt(self, session: str, owner: str) -> None:
        """Give a session's shares to the user who just signed in from it (see Messages.adopt)."""
        with self._engine.begin() as conn:
            conn.execute(text("UPDATE app.shares SET owner = :owner WHERE owner = :session"),
                         {"owner": owner, "session": session})

    @staticmethod
    def _share(row) -> ShareV1:
        return ShareV1(id=row.id, expires_at=row.expires_at, **row.snapshot)
