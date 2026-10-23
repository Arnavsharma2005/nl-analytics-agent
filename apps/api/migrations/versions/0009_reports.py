"""Deep Analysis reports (app/reports.py): one row per report, written as it runs.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-23
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner", sa.Text, nullable=False),  # "user:<id>": reports are for signed-in users
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("stage", sa.Text, nullable=False),
        sa.Column("steps_done", sa.SmallInteger, nullable=False, server_default="0"),
        sa.Column("steps_total", sa.SmallInteger),
        sa.Column("report", postgresql.JSONB),
        sa.Column("error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('running', 'done', 'failed')", name="reports_status"),
        schema="app",
    )
    op.create_index("reports_owner_created", "reports", ["owner", sa.text("created_at DESC")], schema="app")


def downgrade() -> None:
    op.drop_index("reports_owner_created", table_name="reports", schema="app")
    op.drop_table("reports", schema="app")
