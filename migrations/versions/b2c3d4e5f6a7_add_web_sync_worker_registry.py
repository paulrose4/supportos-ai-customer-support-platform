"""add tenant-scoped web sync worker registry and claim indexes"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "e4f5a6b7c8d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "web_sync_worker_registrations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("worker_instance_id", sa.String(length=200), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("health_state", sa.String(length=30), nullable=False, server_default="healthy"),
        sa.Column("draining", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "runtime_version",
            sa.String(length=100),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column("capacity_total", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("capacity_in_use", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "failure_codes",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.CheckConstraint(
            "capacity_total >= 1",
            name="web_sync_worker_capacity_total_positive",
        ),
        sa.CheckConstraint(
            "capacity_in_use >= 0 AND capacity_in_use <= capacity_total",
            name="web_sync_worker_capacity_in_use_valid",
        ),
        sa.CheckConstraint(
            "started_at <= last_seen_at",
            name="web_sync_worker_started_before_last_seen",
        ),
        sa.CheckConstraint(
            "expires_at > last_seen_at",
            name="web_sync_worker_expiry_after_last_seen",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.tenant_id"],
            name="fk_web_sync_worker_registrations_tenant",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "worker_instance_id",
            name="uq_web_sync_worker_registrations_tenant_worker",
        ),
    )
    op.create_index(
        "ix_web_sync_worker_registrations_tenant_expiry",
        "web_sync_worker_registrations",
        ["tenant_id", "expires_at"],
    )
    op.create_index(
        "ix_web_sync_worker_registrations_tenant_health_expiry",
        "web_sync_worker_registrations",
        ["tenant_id", "health_state", "draining", "expires_at"],
    )
    op.execute("ALTER TABLE web_sync_worker_registrations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE web_sync_worker_registrations FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON web_sync_worker_registrations
        USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))
        """
    )

    # The app role is normally covered by default privileges.  Keep upgrades
    # safe for databases where the role/table was created by an older owner.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_tenant') THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE '
                    'public.web_sync_worker_registrations TO app_tenant';
                EXECUTE 'GRANT USAGE, SELECT, UPDATE ON SEQUENCE '
                    'public.web_sync_worker_registrations_id_seq TO app_tenant';
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backup_reader') THEN
                EXECUTE 'GRANT SELECT ON TABLE '
                    'public.web_sync_worker_registrations TO backup_reader';
            END IF;
        END $$;
        """
    )

def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON web_sync_worker_registrations")
    op.drop_index(
        "ix_web_sync_worker_registrations_tenant_health_expiry",
        table_name="web_sync_worker_registrations",
    )
    op.drop_index(
        "ix_web_sync_worker_registrations_tenant_expiry",
        table_name="web_sync_worker_registrations",
    )
    op.drop_table("web_sync_worker_registrations")
