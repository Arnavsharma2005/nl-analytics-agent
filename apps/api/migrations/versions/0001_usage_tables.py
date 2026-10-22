"""Usage counters (quotas and model budgets) and the answer cache.

Revision ID: 0001
Revises:
Create Date: 2026-10-22
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "usage_counters",
        sa.Column("key", sa.Text, primary_key=True),
        sa.Column("window_start", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("count", sa.Integer, nullable=False),
        schema="app",
    )
    op.create_table(
        "answer_cache",
        sa.Column("key", sa.Text, primary_key=True),
        sa.Column("answer", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("hits", sa.Integer, nullable=False, server_default="0"),
        schema="app",
    )


def downgrade() -> None:
    op.drop_table("answer_cache", schema="app")
    op.drop_table("usage_counters", schema="app")
