"""add workforce identity control plane

Revision ID: i9d0e1f2a345
Revises: h8c9d0e1f234
Create Date: 2026-07-27 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "i9d0e1f2a345"
down_revision: str | None = "h8c9d0e1f234"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=30), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", name="uq_tenants_tenant_id"),
    )
    op.create_index("ix_tenants_tenant_id", "tenants", ["tenant_id"])
    op.create_index("ix_tenants_status", "tenants", ["status"])

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=30), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_users_user_id"),
    )
    op.create_index("ix_users_user_id", "users", ["user_id"])
    op.create_index("ix_users_status", "users", ["status"])

    op.create_table(
        "tenant_settings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("primary_language", sa.String(length=20), server_default="zh-CN", nullable=False),
        sa.Column("timezone", sa.String(length=80), server_default="Asia/Shanghai", nullable=False),
        sa.Column("conversation_retention_days", sa.Integer(), server_default="180", nullable=False),
        sa.Column("notification_settings", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", name="uq_tenant_settings_tenant_id"),
    )
    op.create_index("ix_tenant_settings_tenant_id", "tenant_settings", ["tenant_id"])

    op.create_table(
        "tenant_memberships",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("membership_id", sa.String(length=100), nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("user_id", sa.String(length=100), nullable=False),
        sa.Column("roles", sa.JSON(), server_default=sa.text("'[]'::json"), nullable=False),
        sa.Column("scopes", sa.JSON(), server_default=sa.text("'[]'::json"), nullable=False),
        sa.Column("status", sa.String(length=30), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("membership_id", name="uq_tenant_memberships_membership_id"),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_tenant_memberships_tenant_user"),
    )
    for column in ("membership_id", "tenant_id", "user_id", "status"):
        op.create_index(f"ix_tenant_memberships_{column}", "tenant_memberships", [column])

    op.create_table(
        "external_identities",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("identity_id", sa.String(length=100), nullable=False),
        sa.Column("user_id", sa.String(length=100), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("organization_id", sa.String(length=200), nullable=False),
        sa.Column("provider_subject_id", sa.String(length=200), nullable=False),
        sa.Column("provider_user_id", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("identity_id", name="uq_external_identities_identity_id"),
        sa.UniqueConstraint(
            "provider",
            "organization_id",
            "provider_subject_id",
            name="uq_external_identities_provider_org_subject",
        ),
    )
    for column in (
        "identity_id",
        "user_id",
        "provider",
        "organization_id",
        "provider_subject_id",
    ):
        op.create_index(f"ix_external_identities_{column}", "external_identities", [column])

    op.create_table(
        "oauth_login_states",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("state_id", sa.String(length=100), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("return_path", sa.String(length=500), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("state_id", name="uq_oauth_login_states_state_id"),
        sa.UniqueConstraint("state_hash", name="uq_oauth_login_states_state_hash"),
    )
    for column in ("state_id", "provider", "state_hash", "expires_at"):
        op.create_index(f"ix_oauth_login_states_{column}", "oauth_login_states", [column])

    op.create_table(
        "login_completion_grants",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("grant_id", sa.String(length=100), nullable=False),
        sa.Column("user_id", sa.String(length=100), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("grant_id", name="uq_login_completion_grants_grant_id"),
        sa.UniqueConstraint("token_hash", name="uq_login_completion_grants_token_hash"),
    )
    for column in ("grant_id", "user_id", "token_hash", "expires_at"):
        op.create_index(
            f"ix_login_completion_grants_{column}", "login_completion_grants", [column]
        )

    op.create_table(
        "platform_role_assignments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=100), nullable=False),
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=30), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "role", name="uq_platform_role_assignments_user_role"),
    )
    for column in ("user_id", "role", "status"):
        op.create_index(
            f"ix_platform_role_assignments_{column}", "platform_role_assignments", [column]
        )

    op.create_table(
        "organization_identity_bindings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("organization_id", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=30), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "organization_id", name="uq_organization_identity_bindings_provider_org"
        ),
    )
    for column in ("provider", "organization_id", "status"):
        op.create_index(
            f"ix_organization_identity_bindings_{column}",
            "organization_identity_bindings",
            [column],
        )

    op.create_table(
        "platform_audit_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(length=100), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("actor_subject_id", sa.String(length=100), nullable=True),
        sa.Column("correlation_id", sa.String(length=100), nullable=True),
        sa.Column("resource_type", sa.String(length=80), nullable=False),
        sa.Column("resource_id", sa.String(length=200), nullable=False),
        sa.Column("details", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_platform_audit_events_event_id"),
    )
    for column in (
        "event_id",
        "event_type",
        "actor_subject_id",
        "correlation_id",
        "resource_type",
        "resource_id",
        "created_at",
    ):
        op.create_index(f"ix_platform_audit_events_{column}", "platform_audit_events", [column])

    op.create_table(
        "public_widget_registry",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("public_widget_id", sa.String(length=80), nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("site_id", sa.String(length=100), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "allowed_origins",
            sa.JSON(),
            server_default=sa.text("'[]'::json"),
            nullable=False,
        ),
        sa.Column("daily_message_limit", sa.Integer(), server_default="500", nullable=False),
        sa.Column("primary_language", sa.String(length=20), server_default="en", nullable=False),
        sa.Column("status", sa.String(length=30), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_widget_id", name="uq_public_widget_registry_public_id"),
        sa.UniqueConstraint("tenant_id", "site_id", name="uq_public_widget_registry_tenant_site"),
        sa.UniqueConstraint("key_hash", name="uq_public_widget_registry_key_hash"),
    )
    for column in (
        "public_widget_id",
        "tenant_id",
        "site_id",
        "key_hash",
        "status",
    ):
        op.create_index(f"ix_public_widget_registry_{column}", "public_widget_registry", [column])
    op.execute(
        """
        INSERT INTO public_widget_registry
            (public_widget_id, tenant_id, site_id, key_hash, allowed_origins,
             daily_message_limit, primary_language, status, created_at, updated_at)
        SELECT site.public_widget_id, site.tenant_id, site.site_id, credential.key_hash,
               site.allowed_origins, site.widget_daily_message_limit, site.primary_language,
               site.status, site.created_at, site.updated_at
        FROM support_sites site
        LEFT JOIN widget_site_credentials credential
          ON credential.tenant_id = site.tenant_id AND credential.site_id = site.site_id
        ON CONFLICT (public_widget_id) DO NOTHING
        """
    )

    op.add_column("admin_users", sa.Column("global_user_id", sa.String(length=100), nullable=True))
    op.add_column(
        "admin_sessions",
        sa.Column(
            "authentication_method",
            sa.String(length=40),
            server_default="local",
            nullable=False,
        ),
    )

    op.execute(
        """
        INSERT INTO tenants (tenant_id, name, status, created_at, updated_at)
        SELECT tenant_id, tenant_id, 'active', now(), now()
        FROM (
            SELECT tenant_id FROM admin_users
            UNION SELECT tenant_id FROM support_sites
            UNION SELECT tenant_id FROM customers
            UNION SELECT tenant_id FROM audit_events
        ) discovered
        WHERE tenant_id IS NOT NULL AND tenant_id <> '__global__'
        ON CONFLICT (tenant_id) DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE admin_users
        SET global_user_id = 'legacy-' || substr(md5(tenant_id || ':' || user_id), 1, 32)
        WHERE global_user_id IS NULL
        """
    )
    op.execute(
        """
        INSERT INTO users (user_id, display_name, status, created_at, updated_at)
        SELECT global_user_id, display_name, status, created_at, updated_at
        FROM admin_users
        ON CONFLICT (user_id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO tenant_settings
            (tenant_id, primary_language, timezone, conversation_retention_days,
             notification_settings, created_at, updated_at)
        SELECT tenant_id, 'zh-CN', 'Asia/Shanghai', 180, '{}'::json, now(), now()
        FROM tenants
        ON CONFLICT (tenant_id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO tenant_memberships
            (membership_id, tenant_id, user_id, roles, scopes, status, created_at, updated_at)
        SELECT
            'membership-' || substr(md5(tenant_id || ':' || global_user_id), 1, 32),
            tenant_id,
            global_user_id,
            roles,
            scopes,
            status,
            created_at,
            updated_at
        FROM admin_users
        ON CONFLICT (tenant_id, user_id) DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE conversations conversation
        SET assigned_agent_id = admin.global_user_id
        FROM admin_users admin
        WHERE conversation.tenant_id = admin.tenant_id
          AND conversation.assigned_agent_id = admin.user_id
        """
    )
    op.execute(
        """
        UPDATE handoff_requests handoff
        SET assigned_agent_id = admin.global_user_id
        FROM admin_users admin
        WHERE handoff.tenant_id = admin.tenant_id
          AND handoff.assigned_agent_id = admin.user_id
        """
    )

    op.add_column("admin_sessions", sa.Column("migrated_user_id", sa.String(length=100)))
    op.execute(
        """
        UPDATE admin_sessions session
        SET migrated_user_id = admin.global_user_id
        FROM admin_users admin
        WHERE session.tenant_id = admin.tenant_id AND session.user_id = admin.user_id
        """
    )
    op.drop_constraint(
        "fk_admin_sessions_tenant_id_user_id_admin_users",
        "admin_sessions",
        type_="foreignkey",
    )
    op.execute("UPDATE admin_sessions SET user_id = migrated_user_id")
    op.drop_column("admin_sessions", "migrated_user_id")
    op.alter_column("admin_users", "global_user_id", nullable=False)
    op.create_index("ix_admin_users_global_user_id", "admin_users", ["global_user_id"])
    op.create_foreign_key(
        "fk_admin_users_global_user_id_users",
        "admin_users",
        "users",
        ["global_user_id"],
        ["user_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_admin_sessions_tenant_id_user_id_tenant_memberships",
        "admin_sessions",
        "tenant_memberships",
        ["tenant_id", "user_id"],
        ["tenant_id", "user_id"],
        ondelete="CASCADE",
    )
    op.alter_column("admin_sessions", "authentication_method", server_default=None)


def downgrade() -> None:
    op.execute(
        """
        UPDATE conversations conversation
        SET assigned_agent_id = admin.user_id
        FROM admin_users admin
        WHERE conversation.tenant_id = admin.tenant_id
          AND conversation.assigned_agent_id = admin.global_user_id
        """
    )
    op.execute(
        """
        UPDATE handoff_requests handoff
        SET assigned_agent_id = admin.user_id
        FROM admin_users admin
        WHERE handoff.tenant_id = admin.tenant_id
          AND handoff.assigned_agent_id = admin.global_user_id
        """
    )
    op.add_column("admin_sessions", sa.Column("legacy_user_id", sa.String(length=100)))
    op.execute(
        """
        UPDATE admin_sessions session
        SET legacy_user_id = admin.user_id
        FROM admin_users admin
        WHERE session.tenant_id = admin.tenant_id
          AND session.user_id = admin.global_user_id
        """
    )
    op.drop_constraint(
        "fk_admin_sessions_tenant_id_user_id_tenant_memberships",
        "admin_sessions",
        type_="foreignkey",
    )
    op.execute("UPDATE admin_sessions SET user_id = legacy_user_id")
    op.drop_column("admin_sessions", "legacy_user_id")
    op.create_foreign_key(
        "fk_admin_sessions_tenant_id_user_id_admin_users",
        "admin_sessions",
        "admin_users",
        ["tenant_id", "user_id"],
        ["tenant_id", "user_id"],
        ondelete="CASCADE",
    )
    op.drop_column("admin_sessions", "authentication_method")
    op.drop_constraint(
        "fk_admin_users_global_user_id_users", "admin_users", type_="foreignkey"
    )
    op.drop_index("ix_admin_users_global_user_id", table_name="admin_users")
    op.drop_column("admin_users", "global_user_id")

    for table in (
        "public_widget_registry",
        "platform_audit_events",
        "organization_identity_bindings",
        "platform_role_assignments",
        "login_completion_grants",
        "oauth_login_states",
        "external_identities",
        "tenant_memberships",
        "users",
        "tenant_settings",
        "tenants",
    ):
        op.drop_table(table)
