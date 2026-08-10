"""add generic product fact attributes

Revision ID: r8a9b0c1d234
Revises: q7f8a9b0c123
Create Date: 2026-07-31 17:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "r8a9b0c1d234"
down_revision: str | None = "q7f8a9b0c123"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "product_fact_snapshots",
        sa.Column("attributes", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.alter_column("product_fact_snapshots", "attributes", server_default=None)


def downgrade() -> None:
    op.drop_column("product_fact_snapshots", "attributes")
