from datetime import UTC, date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.integrations.postgres.models.base import Base


class SupportSiteModel(Base):
    __tablename__ = "support_sites"
    __table_args__ = (
        UniqueConstraint("tenant_id", "site_id"),
        ForeignKeyConstraint(
            ("tenant_id",),
            ("tenants.tenant_id",),
            name="fk_support_sites_tenant",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    site_id: Mapped[str] = mapped_column(String(100), index=True)
    public_widget_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    base_url: Mapped[str] = mapped_column(String(500))
    allowed_origins: Mapped[list] = mapped_column(JSON, default=list)
    widget_daily_message_limit: Mapped[int] = mapped_column(Integer, default=500)
    primary_language: Mapped[str] = mapped_column(String(20), default="en")
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    verification_status: Mapped[str] = mapped_column(
        String(30), default="verified", server_default="verified", index=True
    )
    duplicate_product_policy: Mapped[str | None] = mapped_column(String(30), nullable=True)
    duplicate_product_order: Mapped[str | None] = mapped_column(String(50), nullable=True)
    verification_method: Mapped[str | None] = mapped_column(String(30), nullable=True)
    verification_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verification_token_prefix: Mapped[str | None] = mapped_column(String(16), nullable=True)
    verification_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    knowledge_publication_state: Mapped[str] = mapped_column(
        String(30), default="active", server_default="active", index=True
    )
    active_knowledge_publication_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True
    )
    pending_knowledge_publication_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True
    )
    knowledge_publication_error: Mapped[str | None] = mapped_column(String(100), nullable=True)
    knowledge_publication_switch_origin: Mapped[str | None] = mapped_column(
        String(30), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class WidgetSiteCredentialModel(Base):
    __tablename__ = "widget_site_credentials"
    __table_args__ = (
        UniqueConstraint("tenant_id", "site_id"),
        UniqueConstraint("key_hash"),
        ForeignKeyConstraint(
            ("tenant_id", "site_id"),
            ("support_sites.tenant_id", "support_sites.site_id"),
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    site_id: Mapped[str] = mapped_column(String(100), index=True)
    key_hash: Mapped[str] = mapped_column(String(64), index=True)
    key_prefix: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WidgetMessageAdmissionModel(Base):
    __tablename__ = "widget_message_admissions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "site_id", "request_id"),
        ForeignKeyConstraint(
            ("tenant_id", "site_id"),
            ("support_sites.tenant_id", "support_sites.site_id"),
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    site_id: Mapped[str] = mapped_column(String(100), index=True)
    request_id: Mapped[str] = mapped_column(String(100), index=True)
    admitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class SiteUsageDailyModel(Base):
    __tablename__ = "site_usage_daily"
    __table_args__ = (
        UniqueConstraint("tenant_id", "site_id", "usage_date"),
        ForeignKeyConstraint(
            ("tenant_id", "site_id"),
            ("support_sites.tenant_id", "support_sites.site_id"),
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    site_id: Mapped[str] = mapped_column(String(100), index=True)
    usage_date: Mapped[date] = mapped_column(Date, index=True)
    admitted_count: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_amount: Mapped[float] = mapped_column(Float, default=0.0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WidgetVisitorSessionModel(Base):
    __tablename__ = "widget_visitor_sessions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "site_id", "session_id"),
        UniqueConstraint("resume_token_hash"),
        ForeignKeyConstraint(
            ("tenant_id", "site_id"),
            ("support_sites.tenant_id", "support_sites.site_id"),
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    site_id: Mapped[str] = mapped_column(String(100), index=True)
    public_widget_id: Mapped[str] = mapped_column(String(80), index=True)
    session_id: Mapped[str] = mapped_column(String(100), index=True)
    origin: Mapped[str] = mapped_column(String(500))
    resume_token_hash: Mapped[str] = mapped_column(String(64), index=True)
    previous_resume_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    previous_valid_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    token_revision: Mapped[int] = mapped_column(Integer, default=1)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PublicWidgetRegistryModel(Base):
    __tablename__ = "public_widget_registry"
    __table_args__ = (
        UniqueConstraint("public_widget_id"),
        UniqueConstraint("tenant_id", "site_id"),
        UniqueConstraint("key_hash"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    public_widget_id: Mapped[str] = mapped_column(String(80), index=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    site_id: Mapped[str] = mapped_column(String(100), index=True)
    key_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    allowed_origins: Mapped[list] = mapped_column(JSON, default=list)
    daily_message_limit: Mapped[int] = mapped_column(Integer, default=500)
    primary_language: Mapped[str] = mapped_column(String(20), default="en")
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    verification_status: Mapped[str] = mapped_column(
        String(30), default="verified", server_default="verified", index=True
    )
    auth_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CustomerMemoryItemModel(Base):
    __tablename__ = "customer_memory_items"
    __table_args__ = (
        UniqueConstraint("tenant_id", "memory_id"),
        ForeignKeyConstraint(
            ("tenant_id", "customer_id"),
            ("customers.tenant_id", "customers.customer_id"),
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    memory_id: Mapped[str] = mapped_column(String(100), index=True)
    customer_id: Mapped[str] = mapped_column(String(100), index=True)
    kind: Mapped[str] = mapped_column(String(50), index=True)
    content: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(50))
    source_id: Mapped[str] = mapped_column(String(100))
    confidence: Mapped[float] = mapped_column(Float)
    consent_status: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    normalized_value: Mapped[dict] = mapped_column(JSON, default=dict)
    sensitivity: Mapped[str] = mapped_column(String(30), default="standard", index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    use_count: Mapped[int] = mapped_column(Integer, default=0)
    superseded_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    legacy: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class MemoryCandidateModel(Base):
    __tablename__ = "memory_candidates"
    __table_args__ = (
        UniqueConstraint("tenant_id", "candidate_id"),
        UniqueConstraint("tenant_id", "customer_id", "candidate_fingerprint"),
        ForeignKeyConstraint(
            ("tenant_id", "customer_id"),
            ("customers.tenant_id", "customers.customer_id"),
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "conversation_id"),
            ("conversations.tenant_id", "conversations.conversation_id"),
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    candidate_id: Mapped[str] = mapped_column(String(100), index=True)
    candidate_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    customer_id: Mapped[str] = mapped_column(String(100), index=True)
    conversation_id: Mapped[str] = mapped_column(String(100), index=True)
    kind: Mapped[str] = mapped_column(String(50), index=True)
    normalized_value: Mapped[dict] = mapped_column(JSON, default=dict)
    display_text: Mapped[str] = mapped_column(Text)
    source_message_ids: Mapped[list] = mapped_column(JSON, default=list)
    source_trace_id: Mapped[str] = mapped_column(String(100), index=True)
    confidence: Mapped[float] = mapped_column(Float)
    sensitivity: Mapped[str] = mapped_column(String(30), default="standard", index=True)
    consent_required: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(30), default="proposed", index=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ResolutionEpisodeModel(Base):
    __tablename__ = "resolution_episodes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "episode_id"),
        UniqueConstraint("tenant_id", "conversation_id", "resolution_source"),
        ForeignKeyConstraint(
            ("tenant_id", "customer_id"),
            ("customers.tenant_id", "customers.customer_id"),
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "conversation_id"),
            ("conversations.tenant_id", "conversations.conversation_id"),
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    episode_id: Mapped[str] = mapped_column(String(100), index=True)
    customer_id: Mapped[str] = mapped_column(String(100), index=True)
    conversation_id: Mapped[str] = mapped_column(String(100), index=True)
    intent: Mapped[str] = mapped_column(String(100), index=True)
    issue_summary: Mapped[str] = mapped_column(Text)
    product_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    order_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    actions_taken: Mapped[list] = mapped_column(JSON, default=list)
    resolution_summary: Mapped[str] = mapped_column(Text)
    resolution_source: Mapped[str] = mapped_column(String(50), index=True)
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    reopened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confidence: Mapped[float] = mapped_column(Float)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)


class SupportOperationRequestModel(Base):
    __tablename__ = "support_operation_requests"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(200), index=True)
    operation: Mapped[str] = mapped_column(String(100))
    resource_type: Mapped[str] = mapped_column(String(100))
    resource_id: Mapped[str] = mapped_column(String(100))
    response_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class SupportQueueModel(Base):
    __tablename__ = "support_queues"
    __table_args__ = (
        UniqueConstraint("tenant_id", "queue_id", name="uq_support_queues_tenant_queue"),
        UniqueConstraint("tenant_id", "name", name="uq_support_queues_tenant_name"),
        ForeignKeyConstraint(
            ("tenant_id", "site_id"),
            ("support_sites.tenant_id", "support_sites.site_id"),
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    queue_id: Mapped[str] = mapped_column(String(100), index=True)
    site_id: Mapped[str | None] = mapped_column(String(100), index=True, nullable=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(500), default="")
    is_default: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SupportQueueMembershipModel(Base):
    __tablename__ = "support_queue_memberships"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "queue_id", "user_id", name="uq_support_queue_memberships_member"
        ),
        ForeignKeyConstraint(
            ("tenant_id", "queue_id"),
            ("support_queues.tenant_id", "support_queues.queue_id"),
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "user_id"),
            ("tenant_memberships.tenant_id", "tenant_memberships.user_id"),
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    queue_id: Mapped[str] = mapped_column(String(100), index=True)
    user_id: Mapped[str] = mapped_column(String(100), index=True)
    role: Mapped[str] = mapped_column(String(30), default="member")
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CannedReplyModel(Base):
    __tablename__ = "canned_replies"
    __table_args__ = (
        UniqueConstraint("tenant_id", "reply_id", name="uq_canned_replies_tenant_reply"),
        UniqueConstraint("tenant_id", "shortcut", name="uq_canned_replies_tenant_shortcut"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    reply_id: Mapped[str] = mapped_column(String(100), index=True)
    title: Mapped[str] = mapped_column(String(160))
    content: Mapped[str] = mapped_column(Text)
    shortcut: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
