"""add administrator session fingerprints

Revision ID: b7f2c9e41d30
Revises: a4b8d6e2c901
Create Date: 2026-07-16 12:15:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7f2c9e41d30"
down_revision: str | None = "a4b8d6e2c901"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "admin_sessions",
        sa.Column(
            "source_fingerprint",
            sa.String(length=64),
            server_default="unknown",
            nullable=False,
        ),
    )
    op.alter_column("admin_sessions", "source_fingerprint", server_default=None)


def downgrade() -> None:
    op.drop_column("admin_sessions", "source_fingerprint")
