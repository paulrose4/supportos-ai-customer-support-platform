from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKeyConstraint, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.integrations.postgres.models.base import Base


class AdminUserModel(Base):
    __tablename__ = "admin_users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id"),
        UniqueConstraint("tenant_id", "username"),
        ForeignKeyConstraint(
            ("global_user_id",),
            ("users.user_id",),
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    user_id: Mapped[str] = mapped_column(String(100), index=True)
    global_user_id: Mapped[str] = mapped_column(String(100), index=True)
    username: Mapped[str] = mapped_column(String(200), index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(500))
    roles: Mapped[list] = mapped_column(JSON, default=list)
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class AdminSessionModel(Base):
    __tablename__ = "admin_sessions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "session_id"),
        UniqueConstraint("token_hash"),
        ForeignKeyConstraint(
            ("tenant_id", "user_id"),
            ("tenant_memberships.tenant_id", "tenant_memberships.user_id"),
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    session_id: Mapped[str] = mapped_column(String(100), index=True)
    user_id: Mapped[str] = mapped_column(String(100), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_fingerprint: Mapped[str] = mapped_column(String(64), default="unknown")
    authentication_method: Mapped[str] = mapped_column(String(40), default="local")
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AdminLoginThrottleModel(Base):
    __tablename__ = "admin_login_throttles"
    __table_args__ = (UniqueConstraint("source_fingerprint"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_failed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_tenant_id: Mapped[str] = mapped_column(String(100))
    last_username: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TenantModel(Base):
    __tablename__ = "tenants"
    __table_args__ = (UniqueConstraint("tenant_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TenantSettingsModel(Base):
    __tablename__ = "tenant_settings"
    __table_args__ = (
        UniqueConstraint("tenant_id"),
        ForeignKeyConstraint(("tenant_id",), ("tenants.tenant_id",), ondelete="CASCADE"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    primary_language: Mapped[str] = mapped_column(String(20), default="zh-CN")
    timezone: Mapped[str] = mapped_column(String(80), default="Asia/Shanghai")
    conversation_retention_days: Mapped[int] = mapped_column(Integer, default=180)
    notification_settings: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class IdentityUserModel(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(100), index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EmailIdentityModel(Base):
    __tablename__ = "email_identities"
    __table_args__ = (
        UniqueConstraint("identity_id"),
        UniqueConstraint("normalized_email"),
        UniqueConstraint("user_id"),
        ForeignKeyConstraint(("user_id",), ("users.user_id",), ondelete="CASCADE"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    identity_id: Mapped[str] = mapped_column(String(100), index=True)
    user_id: Mapped[str] = mapped_column(String(100), index=True)
    normalized_email: Mapped[str] = mapped_column(String(320), index=True)
    display_email: Mapped[str] = mapped_column(String(320))
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PasswordCredentialModel(Base):
    __tablename__ = "password_credentials"
    __table_args__ = (
        UniqueConstraint("user_id"),
        ForeignKeyConstraint(("user_id",), ("users.user_id",), ondelete="CASCADE"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(100), index=True)
    password_hash: Mapped[str] = mapped_column(String(500))
    password_version: Mapped[int] = mapped_column(Integer, default=1)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ExternalIdentityModel(Base):
    __tablename__ = "external_identities"
    __table_args__ = (
        UniqueConstraint("provider", "organization_id", "provider_subject_id"),
        ForeignKeyConstraint(("user_id",), ("users.user_id",), ondelete="CASCADE"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    identity_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(String(100), index=True)
    provider: Mapped[str] = mapped_column(String(40), index=True)
    organization_id: Mapped[str] = mapped_column(String(200), index=True)
    provider_subject_id: Mapped[str] = mapped_column(String(200), index=True)
    provider_user_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TenantMembershipModel(Base):
    __tablename__ = "tenant_memberships"
    __table_args__ = (
        UniqueConstraint("membership_id"),
        UniqueConstraint("tenant_id", "user_id"),
        ForeignKeyConstraint(("tenant_id",), ("tenants.tenant_id",), ondelete="CASCADE"),
        ForeignKeyConstraint(("user_id",), ("users.user_id",), ondelete="CASCADE"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    membership_id: Mapped[str] = mapped_column(String(100), index=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    user_id: Mapped[str] = mapped_column(String(100), index=True)
    roles: Mapped[list] = mapped_column(JSON, default=list)
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    source: Mapped[str] = mapped_column(String(40), default="admin", index=True)
    approval_status: Mapped[str] = mapped_column(String(30), default="approved", index=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OAuthLoginStateModel(Base):
    __tablename__ = "oauth_login_states"
    __table_args__ = (
        UniqueConstraint("state_id"),
        UniqueConstraint("state_hash"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    state_id: Mapped[str] = mapped_column(String(100), index=True)
    provider: Mapped[str] = mapped_column(String(40), index=True)
    state_hash: Mapped[str] = mapped_column(String(64), index=True)
    return_path: Mapped[str] = mapped_column(String(500))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LoginCompletionGrantModel(Base):
    __tablename__ = "login_completion_grants"
    __table_args__ = (
        UniqueConstraint("grant_id"),
        UniqueConstraint("token_hash"),
        ForeignKeyConstraint(("user_id",), ("users.user_id",), ondelete="CASCADE"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    grant_id: Mapped[str] = mapped_column(String(100), index=True)
    user_id: Mapped[str] = mapped_column(String(100), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    authentication_method: Mapped[str] = mapped_column(String(40), default="dingtalk")


class TenantInvitationModel(Base):
    __tablename__ = "tenant_invitations"
    __table_args__ = (
        UniqueConstraint("invitation_id"),
        UniqueConstraint("token_hash"),
        ForeignKeyConstraint(("tenant_id",), ("tenants.tenant_id",), ondelete="CASCADE"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    invitation_id: Mapped[str] = mapped_column(String(100), index=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    normalized_email: Mapped[str] = mapped_column(String(320), index=True)
    display_email: Mapped[str] = mapped_column(String(320))
    roles: Mapped[list] = mapped_column(JSON, default=list)
    token_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    redeemed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PasswordResetTokenModel(Base):
    __tablename__ = "password_reset_tokens"
    __table_args__ = (
        UniqueConstraint("reset_id"),
        UniqueConstraint("token_hash"),
        ForeignKeyConstraint(("user_id",), ("users.user_id",), ondelete="CASCADE"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    reset_id: Mapped[str] = mapped_column(String(100), index=True)
    user_id: Mapped[str] = mapped_column(String(100), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EmailLoginThrottleModel(Base):
    __tablename__ = "email_login_throttles"
    __table_args__ = (UniqueConstraint("source_fingerprint", "email_hash"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    email_hash: Mapped[str] = mapped_column(String(64), index=True)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_failed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PlatformRoleAssignmentModel(Base):
    __tablename__ = "platform_role_assignments"
    __table_args__ = (
        UniqueConstraint("user_id", "role"),
        ForeignKeyConstraint(("user_id",), ("users.user_id",), ondelete="CASCADE"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(100), index=True)
    role: Mapped[str] = mapped_column(String(50), index=True)
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OrganizationIdentityBindingModel(Base):
    __tablename__ = "organization_identity_bindings"
    __table_args__ = (UniqueConstraint("provider", "organization_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(40), index=True)
    organization_id: Mapped[str] = mapped_column(String(200), index=True)
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PlatformAuditEventModel(Base):
    __tablename__ = "platform_audit_events"
    __table_args__ = (UniqueConstraint("event_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(100), index=True)
    event_type: Mapped[str] = mapped_column(String(120), index=True)
    actor_subject_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    correlation_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    resource_type: Mapped[str] = mapped_column(String(80), index=True)
    resource_id: Mapped[str] = mapped_column(String(200), index=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
