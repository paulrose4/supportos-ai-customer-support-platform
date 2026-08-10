"""add support site primary language

Revision ID: e8f1a2b3c4d5
Revises: c9d4e7f1a602
Create Date: 2026-07-19 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e8f1a2b3c4d5"
down_revision: str | None = "c9d4e7f1a602"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "support_sites",
        sa.Column(
            "primary_language",
            sa.String(length=20),
            server_default="en",
            nullable=False,
        ),
    )
    op.alter_column("support_sites", "primary_language", server_default=None)


def downgrade() -> None:
    op.drop_column("support_sites", "primary_language")
