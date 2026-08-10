"""add tenant-scoped widget image assets

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d3e4f5a6b7c8"
down_revision: str | None = "c2d3e4f5a6b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "widget_assets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("site_id", sa.String(length=100), nullable=False),
        sa.Column("asset_id", sa.String(length=100), nullable=False),
        sa.Column("purpose", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("source_content_type", sa.String(length=100), nullable=False),
        sa.Column("source_byte_size", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id"],
            ["support_sites.tenant_id", "support_sites.site_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "asset_id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key"),
    )
    for column in ("tenant_id", "site_id", "asset_id", "purpose", "status", "created_at"):
        op.create_index(f"ix_widget_assets_{column}", "widget_assets", [column])
    op.execute("ALTER TABLE widget_assets ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE widget_assets FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON widget_assets
        USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON widget_assets")
    op.execute("ALTER TABLE widget_assets NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE widget_assets DISABLE ROW LEVEL SECURITY")
    op.drop_table("widget_assets")
