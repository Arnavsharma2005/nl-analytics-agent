"""The Curator's decisions on answers rated thumbs up, kept after the messages they came from expire.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-22
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "curation",
        sa.Column("message_id", UUID(as_uuid=True), primary_key=True),  # no foreign key: messages expire
        sa.Column("status", sa.Text, sa.CheckConstraint("status IN ('approved', 'rejected')"), nullable=False),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("sql", sa.Text, nullable=False),
        sa.Column("reviewer", sa.Text, nullable=False),  # the curator's GitHub login
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("pr_url", sa.Text),  # the pull request it was exported in; null until then
        schema="app",
    )


def downgrade() -> None:
    op.drop_table("curation", schema="app")
