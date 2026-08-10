"""add resumable and observable web sync scheduling state"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MIGRATION_BACKFILL_POLICY = "web_sync_jobs_migration_backfill"


def _backfill_scheduling_state() -> None:
    """Backfill legacy rows through a policy scoped to the migrator role.

    ``web_sync_jobs`` is force-RLS.  A plain migration-role UPDATE therefore
    sees no rows when the role is not a BYPASSRLS superuser.  A temporary
    policy for the current migration role gives the backfill a deliberate,
    auditable exception without toggling RLS off for the table.  The policy is
    dropped before the migration transaction completes.
    """

    op.execute(
        f"""
        DO $migration_policy$
        BEGIN
            EXECUTE 'DROP POLICY IF EXISTS {_MIGRATION_BACKFILL_POLICY} '
                    'ON public.web_sync_jobs';
            EXECUTE 'CREATE POLICY {_MIGRATION_BACKFILL_POLICY} '
                    'ON public.web_sync_jobs FOR ALL TO CURRENT_USER '
                    'USING (true) WITH CHECK (true)';
        END
        $migration_policy$;
        """
    )
    try:
        op.execute(
            """
            UPDATE web_sync_jobs
            SET claim_count = attempt_count,
                updated_at = COALESCE(completed_at, heartbeat_at, started_at, requested_at),
                last_progress_at = COALESCE(completed_at, heartbeat_at, started_at, requested_at),
                available_at = CASE
                    WHEN status IN ('preparing', 'queued') THEN requested_at
                    ELSE NULL
                END
            """
        )
    finally:
        op.execute(f"DROP POLICY IF EXISTS {_MIGRATION_BACKFILL_POLICY} ON public.web_sync_jobs")


def upgrade() -> None:
    op.add_column(
        "web_sync_jobs",
        sa.Column("claim_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "web_sync_jobs",
        sa.Column("failure_attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "web_sync_jobs",
        sa.Column("yield_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "web_sync_jobs",
        sa.Column("state_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "web_sync_jobs",
        sa.Column(
            "prepare_stage",
            sa.String(length=50),
            nullable=False,
            server_default="copy_manifest_items",
        ),
    )
    op.add_column(
        "web_sync_jobs",
        sa.Column("prepare_cursor", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "web_sync_jobs",
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "web_sync_jobs",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.add_column(
        "web_sync_jobs",
        sa.Column("last_progress_at", sa.DateTime(timezone=True), nullable=True),
    )
    _backfill_scheduling_state()
    op.create_check_constraint(
        "web_sync_jobs_scheduling_counts_nonnegative",
        "web_sync_jobs",
        "claim_count >= 0 AND failure_attempt_count >= 0 AND yield_count >= 0 "
        "AND state_version >= 1 AND prepare_cursor >= 0",
    )
def downgrade() -> None:
    op.drop_constraint(
        "web_sync_jobs_scheduling_counts_nonnegative",
        "web_sync_jobs",
        type_="check",
    )
    op.drop_column("web_sync_jobs", "last_progress_at")
    op.drop_column("web_sync_jobs", "updated_at")
    op.drop_column("web_sync_jobs", "available_at")
    op.drop_column("web_sync_jobs", "prepare_cursor")
    op.drop_column("web_sync_jobs", "prepare_stage")
    op.drop_column("web_sync_jobs", "state_version")
    op.drop_column("web_sync_jobs", "yield_count")
    op.drop_column("web_sync_jobs", "failure_attempt_count")
    op.drop_column("web_sync_jobs", "claim_count")
