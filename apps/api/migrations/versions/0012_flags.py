"""Feature flags (app/flags.py): who gets each feature, changed without a redeploy.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-23
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "flags",
        sa.Column("name", sa.Text, primary_key=True),
        sa.Column("audience", sa.Text, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("audience IN ('off', 'curators', 'users', 'everyone')", name="flags_audience"),
        schema="app",
    )


def downgrade() -> None:
    op.drop_table("flags", schema="app")
