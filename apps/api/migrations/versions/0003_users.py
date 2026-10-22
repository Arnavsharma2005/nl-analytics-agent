"""Users who signed in with GitHub, and messages owned by a user or an anonymous session.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("github_id", sa.BigInteger, nullable=False, unique=True),
        sa.Column("login", sa.Text, nullable=False),
        sa.Column("name", sa.Text),
        sa.Column("avatar_url", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="app",
    )
    # "user:<id>" for a signed-in user, otherwise the anonymous session id (which never contains a colon).
    op.alter_column("messages", "session", new_column_name="owner", schema="app")
    op.create_index("messages_owner_idx", "messages", ["owner", "created_at"], schema="app")


def downgrade() -> None:
    op.drop_index("messages_owner_idx", table_name="messages", schema="app")
    op.alter_column("messages", "owner", new_column_name="session", schema="app")
    op.drop_table("users", schema="app")
