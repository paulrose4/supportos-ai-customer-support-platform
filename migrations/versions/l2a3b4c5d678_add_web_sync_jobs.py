"""add durable website sync jobs

Revision ID: l2a3b4c5d678
Revises: k1f2a3b4c567
Create Date: 2026-07-28 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "l2a3b4c5d678"
down_revision: str | None = "k1f2a3b4c567"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "web_sync_jobs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("job_id", sa.String(length=100), nullable=False),
        sa.Column("site_id", sa.String(length=100), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("trigger", sa.String(length=30), nullable=False),
        sa.Column("request_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("requested_by", sa.String(length=100), nullable=False),
        sa.Column("correlation_id", sa.String(length=100), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("active_site_key", sa.String(length=250), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("lease_owner", sa.String(length=200), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("report_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id"],
            ["support_sites.tenant_id", "support_sites.site_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "job_id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key"),
        sa.UniqueConstraint("active_site_key"),
    )
    for column in (
        "tenant_id",
        "job_id",
        "site_id",
        "status",
        "trigger",
        "requested_by",
        "correlation_id",
        "lease_owner",
        "lease_expires_at",
        "requested_at",
        "completed_at",
    ):
        op.create_index(f"ix_web_sync_jobs_{column}", "web_sync_jobs", [column])
    op.execute("ALTER TABLE web_sync_jobs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE web_sync_jobs FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON web_sync_jobs "
        "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')) "
        "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON web_sync_jobs")
    op.drop_table("web_sync_jobs")
