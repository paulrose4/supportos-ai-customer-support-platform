"""add site knowledge publication state

Revision ID: d1e2f3a4b5c6
Revises: c0d1e2f3a4b5
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d1e2f3a4b5c6"
down_revision: str | None = "c0d1e2f3a4b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "support_sites",
        sa.Column(
            "knowledge_publication_state",
            sa.String(length=30),
            nullable=False,
            server_default="active",
        ),
    )
    op.add_column(
        "support_sites",
        sa.Column("active_knowledge_publication_id", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "support_sites",
        sa.Column("pending_knowledge_publication_id", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "support_sites",
        sa.Column("knowledge_publication_error", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "support_sites",
        sa.Column("knowledge_publication_switch_origin", sa.String(length=30), nullable=True),
    )
    op.execute(
        sa.text(
            """
            WITH latest_publications AS (
                SELECT DISTINCT ON (tenant_id, site_id)
                    tenant_id,
                    site_id,
                    job_id
                FROM web_sync_jobs
                WHERE mode = 'production'
                  AND status = 'succeeded'
                  AND publication_status = 'published'
                ORDER BY tenant_id, site_id, completed_at DESC NULLS LAST, id DESC
            ),
            classified AS (
                SELECT
                    site.tenant_id,
                    site.site_id,
                    publication.job_id,
                    publication.job_id IS NOT NULL
                    AND EXISTS (
                        SELECT 1
                        FROM web_sync_job_items AS item
                        WHERE item.tenant_id = publication.tenant_id
                          AND item.job_id = publication.job_id
                          AND item.version_id IS NOT NULL
                    )
                    AND 1 = (
                        SELECT count(*)
                        FROM product_catalog_snapshots AS product_snapshot
                        WHERE product_snapshot.tenant_id = site.tenant_id
                          AND product_snapshot.site_id = site.site_id
                          AND product_snapshot.status = 'active'
                    )
                    AND EXISTS (
                        SELECT 1
                        FROM product_catalog_snapshots AS product_snapshot
                        WHERE product_snapshot.tenant_id = site.tenant_id
                          AND product_snapshot.site_id = site.site_id
                          AND product_snapshot.snapshot_id = publication.job_id
                          AND product_snapshot.status = 'active'
                    )
                    AND NOT EXISTS (
                        SELECT 1
                        FROM web_sync_job_items AS item
                        WHERE item.tenant_id = publication.tenant_id
                          AND item.job_id = publication.job_id
                          AND item.version_id IS NOT NULL
                          AND NOT EXISTS (
                              SELECT 1
                              FROM knowledge_documents AS document
                              WHERE document.tenant_id = item.tenant_id
                                AND document.current_version_id = item.version_id
                          )
                    )
                    AND NOT EXISTS (
                        SELECT 1
                        FROM knowledge_documents AS document
                        JOIN knowledge_document_versions AS version
                          ON version.tenant_id = document.tenant_id
                         AND version.version_id = document.current_version_id
                        WHERE document.tenant_id = site.tenant_id
                          AND version.metadata_payload ->> 'site_id' = site.site_id
                          AND version.metadata_payload ->> 'source_type' = 'website_html'
                          AND (
                              version.status <> 'published'
                              OR version.index_status <> 'active'
                              OR NOT EXISTS (
                                  SELECT 1
                                  FROM web_sync_job_items AS item
                                  WHERE item.tenant_id = document.tenant_id
                                    AND item.job_id = publication.job_id
                                    AND item.version_id = version.version_id
                              )
                          )
                    ) AS is_consistent,
                    publication.job_id IS NOT NULL
                    OR EXISTS (
                        SELECT 1
                        FROM product_catalog_snapshots AS product_snapshot
                        WHERE product_snapshot.tenant_id = site.tenant_id
                          AND product_snapshot.site_id = site.site_id
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM knowledge_documents AS document
                        JOIN knowledge_document_versions AS version
                          ON version.tenant_id = document.tenant_id
                         AND version.version_id = document.current_version_id
                        WHERE document.tenant_id = site.tenant_id
                          AND version.metadata_payload ->> 'site_id' = site.site_id
                          AND version.metadata_payload ->> 'source_type' = 'website_html'
                    ) AS has_publication_data
                FROM support_sites AS site
                LEFT JOIN latest_publications AS publication
                  ON publication.tenant_id = site.tenant_id
                 AND publication.site_id = site.site_id
            )
            UPDATE support_sites AS site
            SET knowledge_publication_state = CASE
                    WHEN classified.is_consistent THEN 'active'
                    WHEN classified.has_publication_data THEN 'recovery_required'
                    ELSE 'active'
                END,
                active_knowledge_publication_id = classified.job_id,
                pending_knowledge_publication_id = NULL,
                knowledge_publication_error = CASE
                    WHEN classified.has_publication_data AND NOT classified.is_consistent
                    THEN 'bootstrap_consistency_audit_required'
                    ELSE NULL
                END,
                knowledge_publication_switch_origin = NULL
            FROM classified
            WHERE site.tenant_id = classified.tenant_id
              AND site.site_id = classified.site_id
            """
        )
    )
    for column in (
        "knowledge_publication_state",
        "active_knowledge_publication_id",
        "pending_knowledge_publication_id",
    ):
        op.create_index(f"ix_support_sites_{column}", "support_sites", [column])


def downgrade() -> None:
    for column in reversed(
        (
            "knowledge_publication_state",
            "active_knowledge_publication_id",
            "pending_knowledge_publication_id",
        )
    ):
        op.drop_index(f"ix_support_sites_{column}", table_name="support_sites")
    op.drop_column("support_sites", "knowledge_publication_error")
    op.drop_column("support_sites", "pending_knowledge_publication_id")
    op.drop_column("support_sites", "active_knowledge_publication_id")
    op.drop_column("support_sites", "knowledge_publication_switch_origin")
    op.drop_column("support_sites", "knowledge_publication_state")
