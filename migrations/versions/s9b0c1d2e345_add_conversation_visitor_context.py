"""add conversation visitor network context

Revision ID: s9b0c1d2e345
Revises: r8a9b0c1d234
Create Date: 2026-07-31 18:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "s9b0c1d2e345"
down_revision: str | None = "r8a9b0c1d234"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("visitor_ip_address", sa.String(length=45), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column("visitor_country_code", sa.String(length=2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversations", "visitor_country_code")
    op.drop_column("conversations", "visitor_ip_address")
