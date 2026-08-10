from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.integrations.postgres.models.base import Base


class EnrollmentAuthorityModel(Base):
    __tablename__ = "enrollment_authorities"
    __table_args__ = (UniqueConstraint("authority_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    authority_id: Mapped[str] = mapped_column(String(100), index=True)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    max_active_tenants: Mapped[int] = mapped_column(Integer)
    max_total_tenants: Mapped[int] = mapped_column(Integer)
    active_tenant_count: Mapped[int] = mapped_column(Integer, default=0)
    reserved_tenant_count: Mapped[int] = mapped_column(Integer, default=0)
    total_tenant_count: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TenantEnrollmentPolicyModel(Base):
    __tablename__ = "tenant_enrollment_policies"
    __table_args__ = (UniqueConstraint("policy_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    policy_id: Mapped[str] = mapped_column(String(100), index=True)
    authority_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("enrollment_authorities.authority_id", ondelete="CASCADE"),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    allowed_email_domains: Mapped[list] = mapped_column(JSON, default=list)
    require_exact_email_binding: Mapped[bool] = mapped_column(Boolean, default=True)
    default_plan_id: Mapped[str] = mapped_column(String(80), default="trial")
    default_role: Mapped[str] = mapped_column(String(40), default="tenant_owner")
    require_email_verification: Mapped[bool] = mapped_column(Boolean, default=True)
    require_mfa: Mapped[bool] = mapped_column(Boolean, default=False)
    site_limit: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TenantEnrollmentCodeModel(Base):
    __tablename__ = "tenant_enrollment_codes"
    __table_args__ = (
        UniqueConstraint("code_id"),
        UniqueConstraint("code_digest"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code_id: Mapped[str] = mapped_column(String(100), index=True)
    policy_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("tenant_enrollment_policies.policy_id", ondelete="CASCADE"),
        index=True,
    )
    code_digest: Mapped[str] = mapped_column(String(64), index=True)
    code_key_version: Mapped[str] = mapped_column(String(30), default="v1")
    target_email: Mapped[str] = mapped_column(String(320), index=True)
    code_prefix: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(30), default="issued", index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    reserved_by_intent_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True
    )
    reserved_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed_by_user_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    consumed_tenant_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EnrollmentIntentModel(Base):
    __tablename__ = "enrollment_intents"
    __table_args__ = (
        UniqueConstraint("intent_id"),
        UniqueConstraint("idempotency_key_hash"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    intent_id: Mapped[str] = mapped_column(String(100), index=True)
    policy_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("tenant_enrollment_policies.policy_id", ondelete="RESTRICT"),
        index=True,
    )
    code_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("tenant_enrollment_codes.code_id", ondelete="RESTRICT"),
        index=True,
    )
    normalized_email: Mapped[str] = mapped_column(String(320), index=True)
    display_email: Mapped[str] = mapped_column(String(320))
    display_name: Mapped[str] = mapped_column(String(200))
    workspace_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str | None] = mapped_column(String(500), nullable=True)
    existing_user_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    proposed_user_id: Mapped[str] = mapped_column(String(100))
    proposed_tenant_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), index=True)
    status_token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(30), default="created", index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EmailVerificationTokenModel(Base):
    __tablename__ = "email_verification_tokens"
    __table_args__ = (
        UniqueConstraint("token_id"),
        UniqueConstraint("token_hash"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    token_id: Mapped[str] = mapped_column(String(100), index=True)
    intent_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("enrollment_intents.intent_id", ondelete="CASCADE"),
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(64), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EnrollmentEmailDeliveryModel(Base):
    __tablename__ = "enrollment_email_deliveries"
    __table_args__ = (UniqueConstraint("delivery_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    delivery_id: Mapped[str] = mapped_column(String(100), index=True)
    intent_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("enrollment_intents.intent_id", ondelete="CASCADE"), index=True
    )
    token_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("email_verification_tokens.token_id", ondelete="CASCADE"),
        index=True,
    )
    recipient: Mapped[str] = mapped_column(String(320))
    display_name: Mapped[str] = mapped_column(String(200))
    workspace_name: Mapped[str] = mapped_column(String(200))
    token_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TenantProvisioningModel(Base):
    __tablename__ = "tenant_provisioning"
    __table_args__ = (
        UniqueConstraint("provisioning_id"),
        UniqueConstraint("user_id", "source"),
        UniqueConstraint("tenant_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provisioning_id: Mapped[str] = mapped_column(String(100), index=True)
    user_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("users.user_id", ondelete="CASCADE"), index=True
    )
    tenant_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), index=True
    )
    source: Mapped[str] = mapped_column(String(40), default="self_service", index=True)
    status: Mapped[str] = mapped_column(String(30), default="completed", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TenantSubscriptionModel(Base):
    __tablename__ = "tenant_subscriptions"
    __table_args__ = (UniqueConstraint("tenant_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), index=True
    )
    plan_id: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(30), default="trial", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TenantQuotaModel(Base):
    __tablename__ = "tenant_quotas"
    __table_args__ = (UniqueConstraint("tenant_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), index=True
    )
    site_limit: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
