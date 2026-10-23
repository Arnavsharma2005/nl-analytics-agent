"""The insights feed: the latest week's notable moves, written nightly by app/jobs/insights.py.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "insights",
        sa.Column("rank", sa.SmallInteger, primary_key=True),  # 1 is the most notable; the job replaces every row
        sa.Column("week_start", sa.Date, nullable=False),
        sa.Column("headline", sa.Text, nullable=False),
        sa.Column("metric", sa.Text, nullable=False),
        sa.Column("dimension", sa.Text),
        sa.Column("grp", sa.Text),
        sa.Column("value", sa.Float, nullable=False),
        sa.Column("usual", sa.Float, nullable=False),
        sa.Column("z", sa.Float, nullable=False),
        sa.Column("sql", sa.Text, nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="app",
    )


def downgrade() -> None:
    op.drop_table("insights", schema="app")
