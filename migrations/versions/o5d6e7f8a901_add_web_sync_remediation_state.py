"""add website sync remediation and artifact state

Revision ID: o5d6e7f8a901
Revises: n4c5d6e7f890
Create Date: 2026-07-29 11:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "o5d6e7f8a901"
down_revision: str | None = "n4c5d6e7f890"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "web_crawl_manifest_items",
        sa.Column(
            "content_kind",
            sa.String(length=30),
            nullable=False,
            server_default="general",
        ),
    )
    op.add_column(
        "web_crawl_manifest_items",
        sa.Column(
            "artifact_status",
            sa.String(length=30),
            nullable=False,
            server_default="unknown",
        ),
    )
    op.create_index(
        "ix_web_crawl_manifest_items_content_kind",
        "web_crawl_manifest_items",
        ["content_kind"],
    )
    op.create_index(
        "ix_web_crawl_manifest_items_artifact_status",
        "web_crawl_manifest_items",
        ["artifact_status"],
    )

    op.add_column(
        "web_sync_job_items",
        sa.Column(
            "content_kind",
            sa.String(length=30),
            nullable=False,
            server_default="general",
        ),
    )
    op.create_index(
        "ix_web_sync_job_items_content_kind",
        "web_sync_job_items",
        ["content_kind"],
    )

    op.add_column(
        "web_sync_jobs",
        sa.Column("blocked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "web_sync_jobs",
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_web_sync_jobs_blocked_at", "web_sync_jobs", ["blocked_at"])
    op.create_index(
        "ix_web_sync_jobs_retention_expires_at",
        "web_sync_jobs",
        ["retention_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_web_sync_jobs_retention_expires_at", table_name="web_sync_jobs")
    op.drop_index("ix_web_sync_jobs_blocked_at", table_name="web_sync_jobs")
    op.drop_column("web_sync_jobs", "retention_expires_at")
    op.drop_column("web_sync_jobs", "blocked_at")

    op.drop_index("ix_web_sync_job_items_content_kind", table_name="web_sync_job_items")
    op.drop_column("web_sync_job_items", "content_kind")

    op.drop_index(
        "ix_web_crawl_manifest_items_artifact_status",
        table_name="web_crawl_manifest_items",
    )
    op.drop_index(
        "ix_web_crawl_manifest_items_content_kind",
        table_name="web_crawl_manifest_items",
    )
    op.drop_column("web_crawl_manifest_items", "artifact_status")
    op.drop_column("web_crawl_manifest_items", "content_kind")
