"""add online indexes for multi-tenant web sync scheduling"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEXES = (
    (
        "ix_web_sync_jobs_active_claim",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_web_sync_jobs_active_claim "
        "ON web_sync_jobs (tenant_id, requested_at, id) "
        "WHERE status IN ('preparing', 'queued', 'running', 'blocked', 'cleanup_pending')",
    ),
    (
        "ix_web_sync_jobs_lease_recovery",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_web_sync_jobs_lease_recovery "
        "ON web_sync_jobs (tenant_id, lease_expires_at, requested_at, id) "
        "WHERE status IN ('preparing', 'running') AND lease_expires_at IS NOT NULL",
    ),
    (
        "ix_web_sync_job_items_pending_due",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_web_sync_job_items_pending_due "
        "ON web_sync_job_items (tenant_id, job_id, next_attempt_at, ordinal, id) "
        "WHERE status = 'pending'",
    ),
    (
        "ix_web_sync_job_items_fetching_recovery",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_web_sync_job_items_fetching_recovery "
        "ON web_sync_job_items (tenant_id, job_id, lease_expires_at, ordinal, id) "
        "WHERE status = 'fetching'",
    ),
    (
        "ix_web_sync_job_items_duplicate_policy",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_web_sync_job_items_duplicate_policy "
        "ON web_sync_job_items (tenant_id, job_id, product_key, ordinal)",
    ),
    (
        "ix_web_sync_jobs_tenant_available",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_web_sync_jobs_tenant_available "
        "ON web_sync_jobs (tenant_id, available_at, requested_at, id) "
        "WHERE status IN ('preparing', 'queued', 'cleanup_pending')",
    ),
    (
        "ix_web_sync_jobs_updated_at",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_web_sync_jobs_updated_at "
        "ON web_sync_jobs (updated_at)",
    ),
    (
        "ix_web_sync_jobs_last_progress_at",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_web_sync_jobs_last_progress_at "
        "ON web_sync_jobs (last_progress_at)",
    ),
)


def _run_autocommit(statement: str) -> None:
    with op.get_context().autocommit_block():
        op.execute(statement)


def _drop_invalid_index(name: str) -> None:
    # Offline SQL generation has no PostgreSQL catalog connection. The
    # concurrent CREATE statement is idempotent, so catalog cleanup is only
    # needed during an online upgrade after an interrupted index build.
    if op.get_context().as_sql:
        return
    invalid = op.get_bind().execute(
        sa.text(
            """
            SELECT NOT index_state.indisvalid
            FROM pg_catalog.pg_index AS index_state
            WHERE index_state.indexrelid = to_regclass(:qualified_name)
            """
        ),
        {"qualified_name": f"public.{name}"},
    ).scalar()
    if invalid:
        _run_autocommit(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")


def upgrade() -> None:
    # This migration contains only online index operations. If PostgreSQL is
    # interrupted, remove any invalid concurrent-index artifact before retry.
    for name, statement in _INDEXES:
        _drop_invalid_index(name)
        _run_autocommit(statement)


def downgrade() -> None:
    for name, _statement in reversed(_INDEXES):
        _run_autocommit(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
