"""add administrator login throttling

Revision ID: f3a1c9d4b702
Revises: e2c4b7a91f06
Create Date: 2026-07-15 19:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3a1c9d4b702"
down_revision: str | None = "e2c4b7a91f06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "admin_login_throttles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_failed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_tenant_id", sa.String(length=100), nullable=False),
        sa.Column("last_username", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_login_throttles")),
        sa.UniqueConstraint(
            "source_fingerprint",
            name=op.f("uq_admin_login_throttles_source_fingerprint"),
        ),
    )
    op.create_index(
        op.f("ix_admin_login_throttles_source_fingerprint"),
        "admin_login_throttles",
        ["source_fingerprint"],
    )
    op.create_index(
        op.f("ix_admin_login_throttles_locked_until"),
        "admin_login_throttles",
        ["locked_until"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_admin_login_throttles_locked_until"),
        table_name="admin_login_throttles",
    )
    op.drop_index(
        op.f("ix_admin_login_throttles_source_fingerprint"),
        table_name="admin_login_throttles",
    )
    op.drop_table("admin_login_throttles")
