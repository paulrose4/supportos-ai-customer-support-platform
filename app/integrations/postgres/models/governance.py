from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.integrations.postgres.models.base import Base


class AuditEventModel(Base):
    __tablename__ = "audit_events"
    __table_args__ = (UniqueConstraint("tenant_id", "event_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    event_id: Mapped[str] = mapped_column(String(100), index=True)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    actor_subject_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(100), index=True, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(100), index=True, nullable=True)
    resource_type: Mapped[str] = mapped_column(String(100))
    resource_id: Mapped[str] = mapped_column(String(100))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )


class OutboxEventModel(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (UniqueConstraint("tenant_id", "event_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    event_id: Mapped[str] = mapped_column(String(100), index=True)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    correlation_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resource_type: Mapped[str] = mapped_column(String(100))
    resource_id: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class HandoffRequestModel(Base):
    __tablename__ = "handoff_requests"
    __table_args__ = (
        UniqueConstraint("tenant_id", "handoff_id"),
        UniqueConstraint("tenant_id", "idempotency_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    handoff_id: Mapped[str] = mapped_column(String(100), index=True)
    customer_id: Mapped[str | None] = mapped_column(String(100), index=True, nullable=True)
    conversation_id: Mapped[str] = mapped_column(String(100), index=True)
    reason_code: Mapped[str] = mapped_column(String(100), index=True)
    risk_level: Mapped[int] = mapped_column(Integer)
    summary: Mapped[str] = mapped_column(Text)
    verified_evidence: Mapped[list] = mapped_column(JSON, default=list)
    failed_tools: Mapped[list] = mapped_column(JSON, default=list)
    knowledge_sources: Mapped[list] = mapped_column(JSON, default=list)
    user_intent: Mapped[str | None] = mapped_column(String(100), nullable=True)
    unresolved_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_attempt: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggested_next_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    reply_draft: Mapped[str | None] = mapped_column(Text, nullable=True)
    queue_id: Mapped[str | None] = mapped_column(String(100), index=True, nullable=True)
    context_schema_version: Mapped[int] = mapped_column(Integer, default=2)
    customer_language: Mapped[str | None] = mapped_column(String(20), nullable=True)
    customer_region: Mapped[str | None] = mapped_column(String(20), nullable=True)
    identity_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    product_ids: Mapped[list] = mapped_column(JSON, default=list)
    order_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    confirmed_fields: Mapped[list] = mapped_column(JSON, default=list)
    customer_sentiment: Mapped[str | None] = mapped_column(String(30), nullable=True)
    customer_request: Mapped[str | None] = mapped_column(Text, nullable=True)
    commitment_deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    priority: Mapped[str] = mapped_column(String(30), default="normal", index=True)
    assigned_agent_id: Mapped[str | None] = mapped_column(String(100), index=True, nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(64))
    trace_id: Mapped[str] = mapped_column(String(100), index=True)
    sla_policy_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    expected_response_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
