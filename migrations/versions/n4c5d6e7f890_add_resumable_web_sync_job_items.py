"""add resumable website sync job items

Revision ID: n4c5d6e7f890
Revises: m3b4c5d6e789
Create Date: 2026-07-28 17:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "n4c5d6e7f890"
down_revision: str | None = "m3b4c5d6e789"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "web_sync_jobs",
        sa.Column("phase", sa.String(length=30), nullable=False, server_default="queued"),
    )
    for name in (
        "expected_count",
        "completed_count",
        "succeeded_count",
        "not_modified_count",
        "excluded_item_count",
        "failed_item_count",
        "canceled_item_count",
    ):
        op.add_column(
            "web_sync_jobs",
            sa.Column(name, sa.Integer(), nullable=False, server_default="0"),
        )
    op.add_column(
        "web_sync_jobs",
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_web_sync_jobs_phase", "web_sync_jobs", ["phase"])
    op.create_index(
        "ix_web_sync_jobs_cancel_requested_at",
        "web_sync_jobs",
        ["cancel_requested_at"],
    )

    op.create_table(
        "web_sync_job_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("job_id", sa.String(length=100), nullable=False),
        sa.Column("site_id", sa.String(length=100), nullable=False),
        sa.Column("manifest_id", sa.String(length=100), nullable=False),
        sa.Column("item_id", sa.String(length=100), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("source_sitemap_url", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("lease_owner", sa.String(length=200), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("document_id", sa.String(length=100), nullable=True),
        sa.Column("version_id", sa.String(length=100), nullable=True),
        sa.Column("canonical_url", sa.Text(), nullable=True),
        sa.Column("final_url", sa.Text(), nullable=True),
        sa.Column("etag", sa.String(length=500), nullable=True),
        sa.Column("last_modified", sa.String(length=200), nullable=True),
        sa.Column("product_key", sa.String(length=200), nullable=True),
        sa.Column(
            "report_payload",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            ["web_sync_jobs.tenant_id", "web_sync_jobs.job_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "job_id", "item_id"),
        sa.UniqueConstraint("tenant_id", "job_id", "url"),
    )
    for column in (
        "tenant_id",
        "job_id",
        "site_id",
        "manifest_id",
        "item_id",
        "status",
        "lease_owner",
        "lease_expires_at",
        "completed_at",
        "product_key",
    ):
        op.create_index(f"ix_web_sync_job_items_{column}", "web_sync_job_items", [column])
    op.execute("ALTER TABLE web_sync_job_items ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE web_sync_job_items FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON web_sync_job_items "
        "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')) "
        "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON web_sync_job_items")
    op.drop_table("web_sync_job_items")
    op.drop_index("ix_web_sync_jobs_cancel_requested_at", table_name="web_sync_jobs")
    op.drop_index("ix_web_sync_jobs_phase", table_name="web_sync_jobs")
    op.drop_column("web_sync_jobs", "cancel_requested_at")
    for name in reversed(
        (
            "expected_count",
            "completed_count",
            "succeeded_count",
            "not_modified_count",
            "excluded_item_count",
            "failed_item_count",
            "canceled_item_count",
        )
    ):
        op.drop_column("web_sync_jobs", name)
    op.drop_column("web_sync_jobs", "phase")
