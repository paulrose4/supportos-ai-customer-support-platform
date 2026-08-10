"""add product catalog snapshots

Revision ID: k1f2a3b4c567
Revises: j0e1f2a3b456
Create Date: 2026-07-27 15:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "k1f2a3b4c567"
down_revision: str | None = "j0e1f2a3b456"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "product_catalog_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("site_id", sa.String(length=100), nullable=False),
        sa.Column("snapshot_id", sa.String(length=100), nullable=False),
        sa.Column("sync_job_id", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("staged_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pending_removal_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expired_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_summary", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "site_id", "snapshot_id"),
    )
    op.create_index(
        "uq_product_catalog_active_site",
        "product_catalog_snapshots",
        ["tenant_id", "site_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    for column in ("tenant_id", "site_id", "snapshot_id", "sync_job_id", "status"):
        op.create_index(
            f"ix_product_catalog_snapshots_{column}",
            "product_catalog_snapshots",
            [column],
        )

    op.create_table(
        "product_fact_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("site_id", sa.String(length=100), nullable=False),
        sa.Column("snapshot_id", sa.String(length=100), nullable=False),
        sa.Column("product_key", sa.String(length=500), nullable=False),
        sa.Column("sku", sa.String(length=200), nullable=True),
        sa.Column("mpn", sa.String(length=200), nullable=True),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("canonical_path", sa.String(length=500), nullable=False),
        sa.Column("brand", sa.String(length=300), nullable=True),
        sa.Column("material", sa.Text(), nullable=True),
        sa.Column("dimensions", sa.JSON(), nullable=False),
        sa.Column("weight", sa.String(length=200), nullable=True),
        sa.Column("price", sa.String(length=100), nullable=True),
        sa.Column("currency", sa.String(length=20), nullable=True),
        sa.Column("stock_status", sa.String(length=200), nullable=True),
        sa.Column("shipping_warehouse", sa.String(length=300), nullable=True),
        sa.Column("shipping_regions", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("etag", sa.String(length=500), nullable=True),
        sa.Column("last_modified", sa.String(length=500), nullable=True),
        sa.Column("data_status", sa.String(length=30), nullable=False),
        sa.Column("missing_count", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id", "snapshot_id"],
            [
                "product_catalog_snapshots.tenant_id",
                "product_catalog_snapshots.site_id",
                "product_catalog_snapshots.snapshot_id",
            ],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "site_id", "snapshot_id", "product_key"),
    )
    for column in (
        "tenant_id",
        "site_id",
        "snapshot_id",
        "product_key",
        "sku",
        "mpn",
        "canonical_path",
        "fetched_at",
        "content_hash",
        "data_status",
    ):
        op.create_index(
            f"ix_product_fact_snapshots_{column}",
            "product_fact_snapshots",
            [column],
        )

    for table in ("product_catalog_snapshots", "product_fact_snapshots"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')) "
            "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))"
        )


def downgrade() -> None:
    for table in ("product_fact_snapshots", "product_catalog_snapshots"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    op.drop_table("product_fact_snapshots")
    op.drop_table("product_catalog_snapshots")
