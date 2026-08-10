"""scale immutable web synchronization manifests

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2f3a4b5c6d7"
down_revision: str | None = "d1e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE web_crawl_manifest_version_seq")
    op.add_column(
        "web_crawl_manifests",
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("nextval('web_crawl_manifest_version_seq')"),
        ),
    )
    op.add_column(
        "web_crawl_manifests",
        sa.Column(
            "policy_version",
            sa.String(length=100),
            nullable=False,
            server_default="web-crawl-v1",
        ),
    )
    op.add_column(
        "web_crawl_manifests",
        sa.Column("url_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "web_crawl_manifests",
        sa.Column(
            "content_kind_counts",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
    )
    op.execute(
        """
        UPDATE web_crawl_manifests AS manifest
        SET url_count = aggregate.url_count,
            content_kind_counts = aggregate.content_kind_counts
        FROM (
            SELECT tenant_id,
                   site_id,
                   manifest_id,
                   SUM(kind_count)::integer AS url_count,
                   json_object_agg(content_kind, kind_count) AS content_kind_counts
            FROM (
                SELECT tenant_id,
                       site_id,
                       manifest_id,
                       content_kind,
                       COUNT(*)::integer AS kind_count
                FROM web_crawl_manifest_items
                GROUP BY tenant_id, site_id, manifest_id, content_kind
            ) AS kind_totals
            GROUP BY tenant_id, site_id, manifest_id
        ) AS aggregate
        WHERE aggregate.tenant_id = manifest.tenant_id
          AND aggregate.site_id = manifest.site_id
          AND aggregate.manifest_id = manifest.manifest_id
        """
    )
    op.add_column(
        "web_sync_jobs",
        sa.Column("prepared_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "web_sync_jobs",
        sa.Column("manifest_version", sa.Integer(), nullable=True),
    )
    op.add_column(
        "web_sync_jobs",
        sa.Column("manifest_fingerprint", sa.String(length=64), nullable=True),
    )
    op.execute("UPDATE web_sync_jobs SET prepared_count = expected_count")
    op.execute(
        """
        UPDATE web_sync_jobs
        SET manifest_fingerprint = request_payload ->> 'manifest_fingerprint'
        WHERE manifest_fingerprint IS NULL
        """
    )
    op.execute(
        """
        UPDATE web_sync_jobs AS job
        SET manifest_version = manifest.version
        FROM web_crawl_manifests AS manifest
        WHERE manifest.tenant_id = job.tenant_id
          AND manifest.site_id = job.site_id
          AND manifest.manifest_id = job.manifest_id
        """
    )
    op.add_column(
        "web_sync_job_items",
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "web_sync_job_items",
        sa.Column("outcome_reason", sa.String(length=100), nullable=True),
    )
    op.create_index(
        "ix_web_sync_job_items_next_attempt_at",
        "web_sync_job_items",
        ["next_attempt_at"],
    )
    op.create_index(
        "ix_web_sync_job_items_outcome_reason",
        "web_sync_job_items",
        ["outcome_reason"],
    )

    op.create_table(
        "web_crawl_page_states",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("site_id", sa.String(length=100), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("document_id", sa.String(length=100), nullable=False),
        sa.Column("version_id", sa.String(length=100), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("final_url", sa.Text(), nullable=False),
        sa.Column("etag", sa.String(length=500), nullable=True),
        sa.Column("last_modified", sa.String(length=200), nullable=True),
        sa.Column("product_key", sa.String(length=200), nullable=True),
        sa.Column(
            "artifact_status",
            sa.String(length=30),
            nullable=False,
            server_default="published",
        ),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id"],
            ["support_sites.tenant_id", "support_sites.site_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "site_id", "url"),
    )
    for column in ("tenant_id", "site_id", "product_key", "artifact_status", "updated_at"):
        op.create_index(
            f"ix_web_crawl_page_states_{column}",
            "web_crawl_page_states",
            [column],
        )
    op.execute(
        """
        INSERT INTO web_crawl_page_states (
            tenant_id, site_id, url, document_id, version_id, canonical_url,
            final_url, etag, last_modified, product_key, artifact_status,
            validated_at, updated_at
        )
        SELECT DISTINCT ON (item.tenant_id, item.site_id, item.url)
            item.tenant_id,
            item.site_id,
            item.url,
            item.document_id,
            item.version_id,
            item.canonical_url,
            COALESCE(item.final_url, item.canonical_url),
            item.etag,
            item.response_last_modified,
            item.product_key,
            'published',
            item.validated_at,
            COALESCE(item.validated_at, manifest.created_at)
        FROM web_crawl_manifest_items AS item
        JOIN web_crawl_manifests AS manifest
          ON manifest.tenant_id = item.tenant_id
         AND manifest.site_id = item.site_id
         AND manifest.manifest_id = item.manifest_id
        WHERE item.document_id IS NOT NULL
          AND item.version_id IS NOT NULL
          AND item.canonical_url IS NOT NULL
          AND item.artifact_status = 'published'
        ORDER BY item.tenant_id, item.site_id, item.url,
                 item.validated_at DESC NULLS LAST, manifest.created_at DESC
        """
    )
    op.execute("ALTER TABLE web_crawl_page_states ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE web_crawl_page_states FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON web_crawl_page_states
        USING (
            tenant_id = NULLIF(current_setting('app.tenant_id', true), '')
            OR (tenant_id = '__global__'
                AND current_setting('app.global_access', true) = 'on')
        )
        WITH CHECK (
            tenant_id = NULLIF(current_setting('app.tenant_id', true), '')
            OR (tenant_id = '__global__'
                AND current_setting('app.global_access', true) = 'on')
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON web_crawl_page_states")
    op.drop_table("web_crawl_page_states")
    op.drop_index("ix_web_sync_job_items_outcome_reason", table_name="web_sync_job_items")
    op.drop_index("ix_web_sync_job_items_next_attempt_at", table_name="web_sync_job_items")
    op.drop_column("web_sync_job_items", "outcome_reason")
    op.drop_column("web_sync_job_items", "next_attempt_at")
    op.drop_column("web_sync_jobs", "manifest_fingerprint")
    op.drop_column("web_sync_jobs", "manifest_version")
    op.drop_column("web_sync_jobs", "prepared_count")
    op.drop_column("web_crawl_manifests", "content_kind_counts")
    op.drop_column("web_crawl_manifests", "url_count")
    op.drop_column("web_crawl_manifests", "policy_version")
    op.drop_column("web_crawl_manifests", "version")
    op.execute("DROP SEQUENCE web_crawl_manifest_version_seq")
