"""Share links: read-only snapshots of an answer.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-22
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shares",
        sa.Column("id", sa.Text, primary_key=True),  # the unguessable token in the link
        sa.Column("owner", sa.Text, nullable=False),  # as in app.messages
        sa.Column("message_id", UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("snapshot", JSONB, nullable=False),  # app.shares.ShareV1 without its id
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        schema="app",
    )
    op.create_index("shares_expires_idx", "shares", ["expires_at"], schema="app")


def downgrade() -> None:
    op.drop_table("shares", schema="app")
