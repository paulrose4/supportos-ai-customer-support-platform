"""add code-gated self-service tenant onboarding

Revision ID: z6c7d8e9f0a1
Revises: y5b6c7d8e9f0
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "z6c7d8e9f0a1"
down_revision: str | None = "y5b6c7d8e9f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenant_memberships",
        sa.Column("source", sa.String(length=40), nullable=False, server_default="admin"),
    )
    op.add_column(
        "tenant_memberships",
        sa.Column("approval_status", sa.String(length=30), nullable=False, server_default="approved"),
    )
    op.add_column("tenant_memberships", sa.Column("activated_at", sa.DateTime(timezone=True)))
    op.add_column("tenant_memberships", sa.Column("deactivated_at", sa.DateTime(timezone=True)))
    op.add_column("tenant_memberships", sa.Column("created_by", sa.String(length=100)))
    op.execute(
        "UPDATE tenant_memberships SET activated_at = created_at WHERE status = 'active'"
    )
    for column in ("source", "approval_status", "activated_at", "deactivated_at", "created_by"):
        op.create_index(f"ix_tenant_memberships_{column}", "tenant_memberships", [column])
    op.alter_column("tenant_memberships", "source", server_default=None)
    op.alter_column("tenant_memberships", "approval_status", server_default=None)

    op.create_table(
        "enrollment_authorities",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("authority_id", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("max_active_tenants", sa.Integer(), nullable=False),
        sa.Column("max_total_tenants", sa.Integer(), nullable=False),
        sa.Column("active_tenant_count", sa.Integer(), nullable=False),
        sa.Column("reserved_tenant_count", sa.Integer(), nullable=False),
        sa.Column("total_tenant_count", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("authority_id"),
        sa.CheckConstraint("max_active_tenants > 0", name="ck_enrollment_authority_active_limit"),
        sa.CheckConstraint("max_total_tenants >= max_active_tenants", name="ck_enrollment_authority_total_limit"),
    )
    op.create_index("ix_enrollment_authorities_authority_id", "enrollment_authorities", ["authority_id"])
    op.create_index("ix_enrollment_authorities_status", "enrollment_authorities", ["status"])

    op.create_table(
        "tenant_enrollment_policies",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("policy_id", sa.String(length=100), nullable=False),
        sa.Column("authority_id", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("allowed_email_domains", sa.JSON(), nullable=False),
        sa.Column("require_exact_email_binding", sa.Boolean(), nullable=False),
        sa.Column("default_plan_id", sa.String(length=80), nullable=False),
        sa.Column("default_role", sa.String(length=40), nullable=False),
        sa.Column("require_email_verification", sa.Boolean(), nullable=False),
        sa.Column("require_mfa", sa.Boolean(), nullable=False),
        sa.Column("site_limit", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["authority_id"], ["enrollment_authorities.authority_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("policy_id"),
        sa.CheckConstraint("default_role = 'tenant_owner'", name="ck_enrollment_policy_owner_role"),
        sa.CheckConstraint("site_limit > 0", name="ck_enrollment_policy_site_limit"),
    )
    for column in ("policy_id", "authority_id", "enabled"):
        op.create_index(f"ix_tenant_enrollment_policies_{column}", "tenant_enrollment_policies", [column])

    op.create_table(
        "tenant_enrollment_codes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code_id", sa.String(length=100), nullable=False),
        sa.Column("policy_id", sa.String(length=100), nullable=False),
        sa.Column("code_digest", sa.String(length=64), nullable=False),
        sa.Column("code_key_version", sa.String(length=30), nullable=False),
        sa.Column("target_email", sa.String(length=320), nullable=False),
        sa.Column("code_prefix", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reserved_by_intent_id", sa.String(length=100)),
        sa.Column("reserved_until", sa.DateTime(timezone=True)),
        sa.Column("consumed_by_user_id", sa.String(length=100)),
        sa.Column("consumed_tenant_id", sa.String(length=100)),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_by", sa.String(length=100)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["policy_id"], ["tenant_enrollment_policies.policy_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code_id"),
        sa.UniqueConstraint("code_digest"),
    )
    for column in ("code_id", "policy_id", "code_digest", "target_email", "status", "expires_at", "reserved_by_intent_id"):
        op.create_index(f"ix_tenant_enrollment_codes_{column}", "tenant_enrollment_codes", [column])

    op.create_table(
        "enrollment_intents",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("intent_id", sa.String(length=100), nullable=False),
        sa.Column("policy_id", sa.String(length=100), nullable=False),
        sa.Column("code_id", sa.String(length=100), nullable=False),
        sa.Column("normalized_email", sa.String(length=320), nullable=False),
        sa.Column("display_email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("workspace_name", sa.String(length=200), nullable=False),
        sa.Column("password_hash", sa.String(length=500)),
        sa.Column("existing_user_id", sa.String(length=100)),
        sa.Column("proposed_user_id", sa.String(length=100), nullable=False),
        sa.Column("proposed_tenant_id", sa.String(length=100), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("status_token_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["policy_id"], ["tenant_enrollment_policies.policy_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["code_id"], ["tenant_enrollment_codes.code_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("intent_id"),
        sa.UniqueConstraint("idempotency_key_hash"),
        sa.UniqueConstraint("proposed_tenant_id"),
        sa.UniqueConstraint("status_token_hash"),
    )
    for column in ("intent_id", "policy_id", "code_id", "normalized_email", "existing_user_id", "proposed_tenant_id", "idempotency_key_hash", "status_token_hash", "status", "expires_at"):
        op.create_index(f"ix_enrollment_intents_{column}", "enrollment_intents", [column])
    op.create_index(
        "uq_enrollment_intents_open_email",
        "enrollment_intents",
        ["normalized_email"],
        unique=True,
        postgresql_where=sa.text("status IN ('created', 'verification_sent', 'email_verified')"),
    )

    op.create_table(
        "email_verification_tokens",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("token_id", sa.String(length=100), nullable=False),
        sa.Column("intent_id", sa.String(length=100), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["intent_id"], ["enrollment_intents.intent_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_id"),
        sa.UniqueConstraint("token_hash"),
    )
    for column in ("token_id", "intent_id", "token_hash", "expires_at"):
        op.create_index(f"ix_email_verification_tokens_{column}", "email_verification_tokens", [column])

    op.create_table(
        "enrollment_email_deliveries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("delivery_id", sa.String(length=100), nullable=False),
        sa.Column("intent_id", sa.String(length=100), nullable=False),
        sa.Column("token_id", sa.String(length=100), nullable=False),
        sa.Column("recipient", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("workspace_name", sa.String(length=200), nullable=False),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["intent_id"], ["enrollment_intents.intent_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["token_id"], ["email_verification_tokens.token_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("delivery_id"),
    )
    for column in ("delivery_id", "intent_id", "token_id", "status", "available_at"):
        op.create_index(f"ix_enrollment_email_deliveries_{column}", "enrollment_email_deliveries", [column])

    op.create_table(
        "tenant_provisioning",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provisioning_id", sa.String(length=100), nullable=False),
        sa.Column("user_id", sa.String(length=100), nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provisioning_id"),
        sa.UniqueConstraint("user_id", "source"),
        sa.UniqueConstraint("tenant_id"),
    )
    for column in ("provisioning_id", "user_id", "tenant_id", "source", "status"):
        op.create_index(f"ix_tenant_provisioning_{column}", "tenant_provisioning", [column])

    op.create_table(
        "tenant_subscriptions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("plan_id", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id"),
    )
    op.create_index("ix_tenant_subscriptions_tenant_id", "tenant_subscriptions", ["tenant_id"])
    op.create_index("ix_tenant_subscriptions_status", "tenant_subscriptions", ["status"])

    op.create_table(
        "tenant_quotas",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("site_limit", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id"),
        sa.CheckConstraint("site_limit > 0", name="ck_tenant_quotas_site_limit"),
    )
    op.create_index("ix_tenant_quotas_tenant_id", "tenant_quotas", ["tenant_id"])

    for table in ("tenant_provisioning", "tenant_subscriptions", "tenant_quotas"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))
            WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))
            """
        )


def downgrade() -> None:
    for table in ("tenant_provisioning", "tenant_subscriptions", "tenant_quotas"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    for table in (
        "tenant_quotas",
        "tenant_subscriptions",
        "tenant_provisioning",
        "enrollment_email_deliveries",
        "email_verification_tokens",
        "enrollment_intents",
        "tenant_enrollment_codes",
        "tenant_enrollment_policies",
        "enrollment_authorities",
    ):
        op.drop_table(table)
    for column in ("created_by", "deactivated_at", "activated_at", "approval_status", "source"):
        op.drop_column("tenant_memberships", column)
