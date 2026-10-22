"""Where a dataset came from, when it can be imported again (a Google Sheets link).

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("datasets", sa.Column("source", sa.Text), schema="app")


def downgrade() -> None:
    op.drop_column("datasets", "source", schema="app")
