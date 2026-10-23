"""API keys: call the /v1 routes from a script, an SDK or an embedded widget instead of a browser session.

A signed-in user makes a key on the web app and sends it as `Authorization: Bearer aak_...`. A request with a key
asks as that user, under that user's question limits. Only a hash of the key is stored; the key itself is shown
once, when it is made. A key can be revoked, and a revoked key stops working on the next request.

A key may list the web origins allowed to use it (for a widget on someone's site). A browser request from any
other origin is refused. A request with no Origin header (a script or a server) is not a browser request and is
allowed: an allowlist stops other sites from embedding a key, not someone who copies it into a script, so a key
put in a web page should be treated as public and keep the user's limits low.
"""

import hashlib
import secrets
from datetime import datetime
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.engine import Engine

PREFIX = "aak_"
PER_USER = 5


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def origin_of(value: str) -> str:
    """`value` as an origin (scheme://host[:port]); raises ValueError when it is not an http(s) origin."""
    parts = urlsplit(value.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.path not in ("", "/") or parts.query \
            or parts.fragment or parts.username:
        raise ValueError(f"{value!r} is not an origin like https://example.com")
    return f"{parts.scheme}://{parts.hostname}" + (f":{parts.port}" if parts.port else "")


class KeyRequestV1(BaseModel):
    name: str = Field(min_length=1, max_length=60, description="What the key is for, e.g. \"Sales dashboard\".")
    origins: list[str] = Field(default=[], max_length=10,
                               description="Web origins allowed to use the key from a browser, e.g. "
                                           "[\"https://example.com\"]. Empty allows any.")

    @field_validator("origins")
    @classmethod
    def _origins(cls, value: list[str]) -> list[str]:
        return sorted({origin_of(v) for v in value})


class KeyV1(BaseModel):
    """An API key as listed: never the key itself."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    id: UUID
    name: str
    hint: str = Field(description="The key's first characters, to tell keys apart.")
    origins: list[str]
    created_at: datetime
    last_used_at: datetime | None


class NewKeyV1(KeyV1):
    key: str = Field(description="The key. It is shown only this once: store it somewhere safe.")


class KeyRefused(Exception):
    """The key is unknown or revoked (`origin` False), or the request's origin is not allowed (`origin` True)."""

    def __init__(self, origin: bool):
        super().__init__("origin not allowed" if origin else "unknown key")
        self.origin = origin


class ApiKeys:
    def __init__(self, engine: Engine):
        self._engine = engine

    def create(self, user_id: int, request: KeyRequestV1) -> NewKeyV1 | None:
        """A new key for the user, or None when they already have PER_USER keys."""
        key = PREFIX + secrets.token_urlsafe(32)
        with self._engine.begin() as conn:
            conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('api_keys'), :user)"), {"user": user_id})
            if conn.execute(text("SELECT count(*) FROM app.api_keys WHERE user_id = :user AND revoked_at IS NULL"),
                            {"user": user_id}).scalar_one() >= PER_USER:
                return None
            row = conn.execute(text("""
                INSERT INTO app.api_keys (id, user_id, name, hash, hint, origins)
                VALUES (:id, :user, :name, :hash, :hint, :origins) RETURNING *
            """), {"id": uuid4(), "user": user_id, "name": request.name.strip(), "hash": _hash(key),
                   "hint": key[:len(PREFIX) + 4], "origins": request.origins}).one()
        return NewKeyV1(**self._listed(row).model_dump(), key=key)

    def list(self, user_id: int) -> list[KeyV1]:
        with self._engine.connect() as conn:
            rows = conn.execute(text("SELECT * FROM app.api_keys WHERE user_id = :user AND revoked_at IS NULL "
                                     "ORDER BY created_at"), {"user": user_id}).all()
        return [self._listed(r) for r in rows]

    def revoke(self, id: UUID, user_id: int) -> bool:
        with self._engine.begin() as conn:
            return conn.execute(text("UPDATE app.api_keys SET revoked_at = now() "
                                     "WHERE id = :id AND user_id = :user AND revoked_at IS NULL"),
                                {"id": id, "user": user_id}).rowcount == 1

    def user_for(self, key: str, origin: str | None) -> int:
        """The id of the key's user; raises KeyRefused for an unknown or revoked key or an origin not allowed."""
        if not key.startswith(PREFIX):
            raise KeyRefused(origin=False)
        with self._engine.begin() as conn:
            row = conn.execute(text("""
                UPDATE app.api_keys SET last_used_at = now()
                WHERE hash = :hash AND revoked_at IS NULL RETURNING user_id, origins
            """), {"hash": _hash(key)}).first()
        if row is None:
            raise KeyRefused(origin=False)
        if origin is not None and row.origins and _origin_or_none(origin) not in row.origins:
            raise KeyRefused(origin=True)
        return row.user_id

    @staticmethod
    def _listed(row) -> KeyV1:
        return KeyV1(id=row.id, name=row.name, hint=row.hint, origins=list(row.origins),
                     created_at=row.created_at, last_used_at=row.last_used_at)


def _origin_or_none(value: str) -> str | None:
    try:
        return origin_of(value)
    except ValueError:
        return None
