"""persist deterministic website product winner decisions"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c1d2e3f4a5b6"
down_revision: str | None = "e2f3a4b5c6d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "web_sync_job_items",
        sa.Column("winner_item_id", sa.String(length=100), nullable=True),
    )
    op.add_column("web_sync_job_items", sa.Column("winner_url", sa.Text(), nullable=True))
    op.add_column(
        "web_sync_job_items",
        sa.Column("identity_source", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "web_sync_job_items",
        sa.Column("policy_version", sa.String(length=100), nullable=True),
    )
    op.create_index(
        "ix_web_sync_job_items_winner_item_id",
        "web_sync_job_items",
        ["winner_item_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_web_sync_job_items_winner_item_id", table_name="web_sync_job_items")
    for column in ("policy_version", "identity_source", "winner_url", "winner_item_id"):
        op.drop_column("web_sync_job_items", column)
