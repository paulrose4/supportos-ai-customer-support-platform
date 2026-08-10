"""add site web source configuration and sitemap discovery metadata

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-08-05 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e4f5a6b7c8d9"
down_revision: str | None = "d3e4f5a6b7c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "site_web_source_configs",
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("site_id", sa.String(length=100), nullable=False),
        sa.Column(
            "discovery_mode",
            sa.String(length=20),
            nullable=False,
            server_default="hybrid",
        ),
        sa.Column(
            "explicit_sitemap_urls",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column("config_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "validation_status",
            sa.String(length=20),
            nullable=False,
            server_default="unvalidated",
        ),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sa.String(length=100), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "discovery_mode IN ('auto', 'hybrid', 'manual')",
            name="ck_site_web_source_configs_discovery_mode",
        ),
        sa.CheckConstraint(
            "validation_status IN ('unvalidated', 'valid', 'invalid')",
            name="ck_site_web_source_configs_validation_status",
        ),
        sa.CheckConstraint(
            "config_version > 0",
            name="ck_site_web_source_configs_config_version",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id"],
            ["support_sites.tenant_id", "support_sites.site_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "site_id"),
    )
    op.create_index(
        "ix_site_web_source_configs_validation_status",
        "site_web_source_configs",
        ["validation_status"],
    )
    op.execute("ALTER TABLE site_web_source_configs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE site_web_source_configs FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON site_web_source_configs "
        "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')) "
        "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))"
    )

    op.add_column(
        "web_crawl_manifests",
        sa.Column(
            "source_config_version",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_check_constraint(
        "ck_web_crawl_manifests_source_config_version",
        "web_crawl_manifests",
        "source_config_version >= 0",
    )
    op.add_column(
        "web_crawl_manifests",
        sa.Column(
            "root_sitemap_urls",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
    )
    op.add_column(
        "web_crawl_manifests",
        sa.Column(
            "discovery_method",
            sa.String(length=30),
            nullable=False,
            server_default="legacy",
        ),
    )
    op.add_column(
        "web_crawl_manifests",
        sa.Column(
            "warnings",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
    )
    op.add_column(
        "web_crawl_manifests",
        sa.Column(
            "coverage_status",
            sa.String(length=30),
            nullable=False,
            server_default="declared_complete",
        ),
    )
    op.execute(
        "UPDATE web_crawl_manifests "
        "SET root_sitemap_urls = json_build_array(root_sitemap_url) "
        "WHERE root_sitemap_url IS NOT NULL"
    )
    op.add_column(
        "web_crawl_manifests",
        sa.Column(
            "discovery_attempts",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
    )


def downgrade() -> None:
    op.drop_column("web_crawl_manifests", "discovery_attempts")
    op.drop_column("web_crawl_manifests", "coverage_status")
    op.drop_column("web_crawl_manifests", "warnings")
    op.drop_column("web_crawl_manifests", "discovery_method")
    op.drop_column("web_crawl_manifests", "root_sitemap_urls")
    op.drop_constraint(
        "ck_web_crawl_manifests_source_config_version",
        "web_crawl_manifests",
        type_="check",
    )
    op.drop_column("web_crawl_manifests", "source_config_version")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON site_web_source_configs")
    op.drop_index(
        "ix_site_web_source_configs_validation_status",
        table_name="site_web_source_configs",
    )
    op.drop_table("site_web_source_configs")
