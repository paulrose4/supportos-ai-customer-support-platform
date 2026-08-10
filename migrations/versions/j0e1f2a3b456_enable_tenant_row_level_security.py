"""enable tenant row level security

Revision ID: j0e1f2a3b456
Revises: i9d0e1f2a345
Create Date: 2026-07-27 12:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "j0e1f2a3b456"
down_revision: str | None = "i9d0e1f2a345"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONTROL_TABLES = (
    "admin_users",
    "admin_sessions",
    "admin_login_throttles",
    "tenants",
    "tenant_settings",
    "users",
    "external_identities",
    "tenant_memberships",
    "oauth_login_states",
    "login_completion_grants",
    "platform_role_assignments",
    "organization_identity_bindings",
    "platform_audit_events",
    "public_widget_registry",
)


def upgrade() -> None:
    excluded = ", ".join(f"'{table}'" for table in _CONTROL_TABLES)
    op.execute(
        f"""
        DO $$
        DECLARE
            tenant_table text;
        BEGIN
            FOR tenant_table IN
                SELECT DISTINCT columns.table_name
                FROM information_schema.columns columns
                WHERE columns.table_schema = current_schema()
                  AND columns.column_name = 'tenant_id'
                  AND columns.table_name NOT IN ({excluded})
            LOOP
                EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', tenant_table);
                EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', tenant_table);
                EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', tenant_table);
                EXECUTE format(
                    'CREATE POLICY tenant_isolation ON %I '
                    'USING ('
                    'tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''') '
                    'OR (tenant_id = ''__global__'' '
                    'AND current_setting(''app.global_access'', true) = ''on'')'
                    ') '
                    'WITH CHECK ('
                    'tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''') '
                    'OR (tenant_id = ''__global__'' '
                    'AND current_setting(''app.global_access'', true) = ''on'')'
                    ')',
                    tenant_table
                );
            END LOOP;
        END $$;
        """
    )


def downgrade() -> None:
    excluded = ", ".join(f"'{table}'" for table in _CONTROL_TABLES)
    op.execute(
        f"""
        DO $$
        DECLARE
            tenant_table text;
        BEGIN
            FOR tenant_table IN
                SELECT DISTINCT columns.table_name
                FROM information_schema.columns columns
                WHERE columns.table_schema = current_schema()
                  AND columns.column_name = 'tenant_id'
                  AND columns.table_name NOT IN ({excluded})
            LOOP
                EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', tenant_table);
                EXECUTE format('ALTER TABLE %I NO FORCE ROW LEVEL SECURITY', tenant_table);
                EXECUTE format('ALTER TABLE %I DISABLE ROW LEVEL SECURITY', tenant_table);
            END LOOP;
        END $$;
        """
    )
