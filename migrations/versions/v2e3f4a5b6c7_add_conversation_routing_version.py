"""add conversation routing version for atomic operator actions

Revision ID: v2e3f4a5b6c7
Revises: u1d2e3f4a5b6
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "v2e3f4a5b6c7"
down_revision: str | None = "u1d2e3f4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("routing_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.alter_column("conversations", "routing_version", server_default=None)


def downgrade() -> None:
    op.drop_column("conversations", "routing_version")
