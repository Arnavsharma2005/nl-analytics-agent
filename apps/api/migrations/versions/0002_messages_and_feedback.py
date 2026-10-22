"""Messages (questions and their answers, for /v1) and feedback on them.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-22
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),  # the client's idempotency key
        sa.Column("conversation_id", UUID(as_uuid=True), nullable=False),
        sa.Column("session", sa.Text),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("answer", JSONB),  # null while the answer is being worked out
        sa.Column("explanation", JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="app",
    )
    op.create_index("messages_conversation_idx", "messages", ["conversation_id", "created_at"], schema="app")
    op.create_index("messages_created_idx", "messages", ["created_at"], schema="app")
    op.create_table(
        "feedback",
        sa.Column("message_id", UUID(as_uuid=True), sa.ForeignKey("app.messages.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("rating", sa.SmallInteger, sa.CheckConstraint("rating IN (-1, 1)"), nullable=False),
        sa.Column("category", sa.Text),
        sa.Column("comment", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="app",
    )


def downgrade() -> None:
    op.drop_table("feedback", schema="app")
    op.drop_table("messages", schema="app")
