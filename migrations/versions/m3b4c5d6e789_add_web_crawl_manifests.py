"""add web crawl manifests and first-class sync modes

Revision ID: m3b4c5d6e789
Revises: l2a3b4c5d678
Create Date: 2026-07-28 14:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "m3b4c5d6e789"
down_revision: str | None = "l2a3b4c5d678"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "web_crawl_manifests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("site_id", sa.String(length=100), nullable=False),
        sa.Column("manifest_id", sa.String(length=100), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("root_sitemap_url", sa.Text(), nullable=False),
        sa.Column("primary_language", sa.String(length=20), nullable=False),
        sa.Column("translation_provider", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("primary_sitemap_urls", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("translated_locales", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("excluded_sitemap_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("excluded_url_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("blocking_reasons", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id"],
            ["support_sites.tenant_id", "support_sites.site_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "site_id", "manifest_id"),
    )
    op.create_table(
        "web_crawl_manifest_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("site_id", sa.String(length=100), nullable=False),
        sa.Column("manifest_id", sa.String(length=100), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("source_sitemap_url", sa.Text(), nullable=False),
        sa.Column("last_modified", sa.String(length=200), nullable=True),
        sa.Column("document_id", sa.String(length=100), nullable=True),
        sa.Column("version_id", sa.String(length=100), nullable=True),
        sa.Column("canonical_url", sa.Text(), nullable=True),
        sa.Column("final_url", sa.Text(), nullable=True),
        sa.Column("etag", sa.String(length=500), nullable=True),
        sa.Column("response_last_modified", sa.String(length=200), nullable=True),
        sa.Column("product_key", sa.String(length=200), nullable=True),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id", "manifest_id"],
            [
                "web_crawl_manifests.tenant_id",
                "web_crawl_manifests.site_id",
                "web_crawl_manifests.manifest_id",
            ],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "site_id", "manifest_id", "url"),
    )
    for table, columns in {
        "web_crawl_manifests": (
            "tenant_id",
            "site_id",
            "manifest_id",
            "status",
            "fingerprint",
            "created_by",
            "created_at",
        ),
        "web_crawl_manifest_items": (
            "tenant_id",
            "site_id",
            "manifest_id",
            "validated_at",
        ),
    }.items():
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')) "
            "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))"
        )

    op.add_column(
        "web_sync_jobs",
        sa.Column("mode", sa.String(length=30), nullable=False, server_default="production"),
    )
    op.add_column(
        "web_sync_jobs",
        sa.Column("manifest_id", sa.String(length=100), nullable=True),
    )
    op.add_column("web_sync_jobs", sa.Column("sample_size", sa.Integer(), nullable=True))
    op.add_column(
        "web_sync_jobs",
        sa.Column(
            "publication_status",
            sa.String(length=30),
            nullable=False,
            server_default="pending",
        ),
    )
    op.create_index("ix_web_sync_jobs_mode", "web_sync_jobs", ["mode"])
    op.create_index("ix_web_sync_jobs_manifest_id", "web_sync_jobs", ["manifest_id"])
    op.create_index(
        "ix_web_sync_jobs_publication_status",
        "web_sync_jobs",
        ["publication_status"],
    )
    op.create_foreign_key(
        "fk_web_sync_jobs_manifest",
        "web_sync_jobs",
        "web_crawl_manifests",
        ["tenant_id", "site_id", "manifest_id"],
        ["tenant_id", "site_id", "manifest_id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_web_sync_jobs_manifest", "web_sync_jobs", type_="foreignkey")
    op.drop_index("ix_web_sync_jobs_publication_status", table_name="web_sync_jobs")
    op.drop_index("ix_web_sync_jobs_manifest_id", table_name="web_sync_jobs")
    op.drop_index("ix_web_sync_jobs_mode", table_name="web_sync_jobs")
    op.drop_column("web_sync_jobs", "publication_status")
    op.drop_column("web_sync_jobs", "sample_size")
    op.drop_column("web_sync_jobs", "manifest_id")
    op.drop_column("web_sync_jobs", "mode")
    for table in ("web_crawl_manifest_items", "web_crawl_manifests"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    op.drop_table("web_crawl_manifest_items")
    op.drop_table("web_crawl_manifests")
