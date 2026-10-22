"""Uploaded datasets, kept as Parquet in the app database for a day (the API host's disk is wiped on every deploy).

Each belongs to its owner (as messages do: a signed-in user or an anonymous session), who alone can see, query or
delete it. An owner can keep a few at once, and all uploads together are capped so the free database never fills.
"""

import json
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.datasets.csv_upload import Upload, UploadRejected
from app.datasets.profile import ColumnV1


class DatasetV1(BaseModel):
    """An uploaded file, ready for questions."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    id: UUID
    name: str
    table: str
    rows: int
    columns: list[ColumnV1]
    suggestions: list[str]
    source: str | None = Field(default=None, description="The Google Sheets link it was imported from, if any; "
                                                         "POST /v1/datasets/{id}/refresh imports it again.")
    created_at: datetime
    expires_at: datetime


# The problem when a dataset is gone (expired, deleted, or someone else's): code, title and detail.
GONE = ("dataset_not_found", "This uploaded file is no longer available",
        "Uploaded files are deleted a day after they were uploaded. Upload it again to keep asking.")

COLUMNS = "id, name, table_name, row_count, columns, suggestions, source, created_at, expires_at"


def _dataset(row) -> DatasetV1:
    return DatasetV1(id=row.id, name=row.name, table=row.table_name, rows=row.row_count, columns=row.columns,
                     suggestions=row.suggestions, source=row.source, created_at=row.created_at,
                     expires_at=row.expires_at)


class Datasets:
    def __init__(self, engine: Engine, ttl_hours: int = 24, per_owner: int = 3, total_mb: int = 200):
        self._engine = engine
        self.ttl = timedelta(hours=ttl_hours)
        self.per_owner = per_owner
        self._total_bytes = total_mb * 1024 * 1024

    def add(self, owner: str, upload: Upload, source: str | None = None) -> DatasetV1:
        """Keep an upload (`source`: the sheet it was imported from). Raises UploadRejected when the owner already
        has as many as they may keep, or when uploads are full."""
        size = len(upload.parquet)
        with self._engine.begin() as conn:
            conn.execute(text("DELETE FROM app.datasets WHERE expires_at < now()"))
            # One upload at a time per owner, so two at once can't both squeeze under the limit.
            conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('datasets:' || :owner))"), {"owner": owner})
            mine = conn.execute(text("SELECT COUNT(*) FROM app.datasets WHERE owner = :owner"),
                                {"owner": owner}).scalar()
            if mine >= self.per_owner:
                raise UploadRejected("too_many_datasets", f"You already have {mine} files uploaded",
                                     "Delete one to upload another. Each is deleted a day after it was uploaded.",
                                     status=409)
            self._check_room(conn, size)
            row = conn.execute(text(f"""
                INSERT INTO app.datasets (id, owner, name, table_name, row_count, columns, suggestions, parquet,
                                          size_bytes, source, expires_at)
                VALUES (:id, :owner, :name, :table, :rows, CAST(:columns AS jsonb), CAST(:suggestions AS jsonb),
                        :parquet, :size, :source, now() + :ttl)
                RETURNING {COLUMNS}
            """), {"id": uuid4(), "owner": owner, "source": source, **self._values(upload)}).one()
        return _dataset(row)

    def replace(self, id: UUID, owner: str, upload: Upload) -> DatasetV1 | None:
        """Put a fresh copy of the owner's dataset in place (an imported sheet, imported again), keeping its id so
        conversations about it carry on, for another full day. None if it is gone."""
        with self._engine.begin() as conn:
            old = conn.execute(text("""
                SELECT size_bytes FROM app.datasets WHERE id = :id AND owner = :owner AND expires_at > now()
                FOR UPDATE
            """), {"id": id, "owner": owner}).first()
            if old is None:
                return None
            self._check_room(conn, len(upload.parquet) - old.size_bytes)
            row = conn.execute(text(f"""
                UPDATE app.datasets SET name = :name, table_name = :table, row_count = :rows,
                       columns = CAST(:columns AS jsonb), suggestions = CAST(:suggestions AS jsonb),
                       parquet = :parquet, size_bytes = :size, expires_at = now() + :ttl
                WHERE id = :id
                RETURNING {COLUMNS}
            """), {"id": id, **self._values(upload)}).one()
        return _dataset(row)

    def _values(self, upload: Upload) -> dict:
        return {"name": upload.name, "table": upload.table, "rows": upload.rows,
                "columns": json.dumps([c.model_dump() for c in upload.columns]),
                "suggestions": json.dumps(upload.suggestions), "parquet": upload.parquet,
                "size": len(upload.parquet), "ttl": self.ttl}

    def _check_room(self, conn, more_bytes: int) -> None:
        used = conn.execute(text("SELECT COALESCE(SUM(size_bytes), 0) FROM app.datasets")).scalar()
        if used + more_bytes > self._total_bytes:
            raise UploadRejected("uploads_full", "Uploads are full right now",
                                 "Uploaded files are deleted after a day, so try again later.", status=503)

    def get(self, id: UUID, owner: str | None) -> DatasetV1 | None:
        if not owner:
            return None
        with self._engine.connect() as conn:
            row = conn.execute(text(f"""
                SELECT {COLUMNS} FROM app.datasets WHERE id = :id AND owner = :owner AND expires_at > now()
            """), {"id": id, "owner": owner}).first()
        return _dataset(row) if row else None

    def parquet(self, id: UUID) -> bytes | None:
        """The data of a dataset whose owner has already been checked with `get`."""
        with self._engine.connect() as conn:
            return conn.execute(text("SELECT parquet FROM app.datasets WHERE id = :id AND expires_at > now()"),
                                {"id": id}).scalar()

    def list(self, owner: str | None) -> list[DatasetV1]:
        if not owner:
            return []
        with self._engine.connect() as conn:
            rows = conn.execute(text(f"""
                SELECT {COLUMNS} FROM app.datasets WHERE owner = :owner AND expires_at > now()
                ORDER BY created_at DESC
            """), {"owner": owner}).all()
        return [_dataset(r) for r in rows]

    def delete(self, id: UUID, owner: str | None) -> bool:
        if not owner:
            return False
        with self._engine.begin() as conn:
            return conn.execute(text("DELETE FROM app.datasets WHERE id = :id AND owner = :owner"),
                                {"id": id, "owner": owner}).rowcount > 0

    def adopt(self, session: str, owner: str) -> None:
        """Give a session's uploads to the user who just signed in from it (see Messages.adopt)."""
        with self._engine.begin() as conn:
            conn.execute(text("UPDATE app.datasets SET owner = :owner WHERE owner = :session"),
                         {"owner": owner, "session": session})
