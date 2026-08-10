from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.integrations.postgres.models.base import Base


class ConversationModel(Base):
    __tablename__ = "conversations"
    __table_args__ = (UniqueConstraint("tenant_id", "conversation_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    conversation_id: Mapped[str] = mapped_column(String(100), index=True)
    customer_id: Mapped[str | None] = mapped_column(String(100), index=True, nullable=True)
    site_id: Mapped[str | None] = mapped_column(String(100), index=True, nullable=True)
    visitor_session_id: Mapped[str | None] = mapped_column(String(100), index=True, nullable=True)
    visitor_ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    visitor_country_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    channel: Mapped[str] = mapped_column(String(50), default="web_api")
    status: Mapped[str] = mapped_column(String(50), default="open")
    ownership_mode: Mapped[str] = mapped_column(String(30), default="ai", index=True)
    assigned_agent_id: Mapped[str | None] = mapped_column(String(100), index=True, nullable=True)
    routing_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    queue_id: Mapped[str | None] = mapped_column(String(100), index=True, nullable=True)
    priority: Mapped[str] = mapped_column(String(30), default="normal", index=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    risk_level: Mapped[int] = mapped_column(Integer, default=0)
    unread_count: Mapped[int] = mapped_column(Integer, default=0)
    identity_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True
    )
    first_response_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    first_human_response_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    auto_resolution_eligible_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True
    )
    auto_resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reopened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_source: Mapped[str | None] = mapped_column(String(50), index=True, nullable=True)
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class MessageModel(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("tenant_id", "message_id"),
        ForeignKeyConstraint(
            ("tenant_id", "conversation_id"),
            ("conversations.tenant_id", "conversations.conversation_id"),
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    message_id: Mapped[str] = mapped_column(String(100), index=True)
    conversation_id: Mapped[str] = mapped_column(String(100), index=True)
    role: Mapped[str] = mapped_column(String(30))
    content: Mapped[str] = mapped_column(Text)
    redaction_status: Mapped[str] = mapped_column(String(30), default="not_required")
    message_type: Mapped[str] = mapped_column(String(30), default="chat")
    author_subject_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    message_metadata: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class AgentRunModel(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "run_id"),
        UniqueConstraint("tenant_id", "trace_id"),
        ForeignKeyConstraint(
            ("tenant_id", "conversation_id"),
            ("conversations.tenant_id", "conversations.conversation_id"),
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    run_id: Mapped[str] = mapped_column(String(100), index=True)
    trace_id: Mapped[str] = mapped_column(String(100), index=True)
    conversation_id: Mapped[str] = mapped_column(String(100), index=True)
    status: Mapped[str] = mapped_column(String(30))
    response_kind: Mapped[str] = mapped_column(String(30))
    risk_level: Mapped[int] = mapped_column(Integer)
    handoff_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    request_received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True
    )
    response_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True
    )
    response_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_route: Mapped[str | None] = mapped_column(String(120), index=True, nullable=True)


class ToolExecutionModel(Base):
    __tablename__ = "tool_executions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "tool_execution_id"),
        ForeignKeyConstraint(
            ("tenant_id", "run_id"),
            ("agent_runs.tenant_id", "agent_runs.run_id"),
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    tool_execution_id: Mapped[str] = mapped_column(String(100), index=True)
    run_id: Mapped[str] = mapped_column(String(100), index=True)
    tool_name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30))
    input_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    output_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class ConversationMemoryModel(Base):
    __tablename__ = "conversation_memories"
    __table_args__ = (
        UniqueConstraint("tenant_id", "conversation_id"),
        ForeignKeyConstraint(
            ("tenant_id", "conversation_id"),
            ("conversations.tenant_id", "conversations.conversation_id"),
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    conversation_id: Mapped[str] = mapped_column(String(100), index=True)
    site_id: Mapped[str] = mapped_column(String(100), index=True)
    working_memory: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    summarized_message_count: Mapped[int] = mapped_column(Integer, default=0)
    last_summarized_message_db_id: Mapped[int] = mapped_column(Integer, default=0)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    last_trace_id: Mapped[str] = mapped_column(String(100), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
