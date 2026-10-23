"""API keys (app/keys.py): a hash of each key, its owner and the origins allowed to use it.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-23
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.BigInteger, sa.ForeignKey("app.users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("hash", sa.Text, nullable=False, unique=True),  # sha256 of the key; the key is never stored
        sa.Column("hint", sa.Text, nullable=False),
        sa.Column("origins", postgresql.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        schema="app",
    )
    op.create_index("api_keys_user", "api_keys", ["user_id"], schema="app")


def downgrade() -> None:
    op.drop_index("api_keys_user", table_name="api_keys", schema="app")
    op.drop_table("api_keys", schema="app")
