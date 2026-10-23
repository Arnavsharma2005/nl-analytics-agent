"""Scheduled reports and alerts (app/schedules.py): a question asked on a schedule, its answer sent by email or
Telegram, or for an alert only when its number crosses a threshold.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-23
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "schedules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.BigInteger, sa.ForeignKey("app.users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("every", sa.Text, nullable=False),
        sa.Column("hour_utc", sa.SmallInteger, nullable=False),
        sa.Column("weekday", sa.SmallInteger, nullable=False),  # 0 is Monday; only used weekly
        sa.Column("channel", sa.Text, nullable=False),
        sa.Column("recipient", sa.Text, nullable=False),
        sa.Column("op", sa.Text),  # alerts only
        sa.Column("threshold", sa.Float),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column("last_status", sa.Text),
        sa.Column("last_value", sa.Float),
        sa.Column("firing", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("kind IN ('report', 'alert')", name="schedules_kind"),
        sa.CheckConstraint("kind = 'report' OR (op IS NOT NULL AND threshold IS NOT NULL)", name="schedules_alert"),
        schema="app",
    )
    op.create_index("schedules_due", "schedules", ["next_run_at"], schema="app")
    op.create_index("schedules_user", "schedules", ["user_id"], schema="app")


def downgrade() -> None:
    op.drop_index("schedules_user", table_name="schedules", schema="app")
    op.drop_index("schedules_due", table_name="schedules", schema="app")
    op.drop_table("schedules", schema="app")
