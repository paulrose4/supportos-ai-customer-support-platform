"""add conversation read cursor

Revision ID: e4b2d7c8a910
Revises: d7a1c5e9f203
Create Date: 2026-07-21 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e4b2d7c8a910"
down_revision: str | None = "d7a1c5e9f203"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversations", sa.Column("last_read_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("conversations", "last_read_at")
