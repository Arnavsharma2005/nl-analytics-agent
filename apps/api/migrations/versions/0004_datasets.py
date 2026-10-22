"""Uploaded datasets (kept as Parquet for a day), and which dataset a message asked about.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-22
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "datasets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("owner", sa.Text, nullable=False),  # as in app.messages
        sa.Column("name", sa.Text, nullable=False),  # the file's name
        sa.Column("table_name", sa.Text, nullable=False),
        sa.Column("row_count", sa.Integer, nullable=False),
        sa.Column("columns", JSONB, nullable=False),  # app.datasets.profile.ColumnV1, one per column
        sa.Column("suggestions", JSONB, nullable=False),
        sa.Column("parquet", sa.LargeBinary, nullable=False),
        sa.Column("size_bytes", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        schema="app",
    )
    op.create_index("datasets_owner_idx", "datasets", ["owner", "created_at"], schema="app")
    op.create_index("datasets_expires_idx", "datasets", ["expires_at"], schema="app")
    op.add_column("messages", sa.Column("dataset_id", UUID(as_uuid=True)), schema="app")


def downgrade() -> None:
    op.drop_column("messages", "dataset_id", schema="app")
    op.drop_table("datasets", schema="app")
