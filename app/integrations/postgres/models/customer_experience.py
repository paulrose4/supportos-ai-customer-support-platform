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


class WidgetConfigVersionModel(Base):
    __tablename__ = "widget_config_versions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "version_id"),
        UniqueConstraint("tenant_id", "site_id", "version_number"),
        ForeignKeyConstraint(
            ("tenant_id", "site_id"),
            ("support_sites.tenant_id", "support_sites.site_id"),
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    site_id: Mapped[str] = mapped_column(String(100), index=True)
    version_id: Mapped[str] = mapped_column(String(100), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), index=True)
    config: Mapped[dict] = mapped_column(JSON)
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AutomationRuleModel(Base):
    __tablename__ = "automation_rules"
    __table_args__ = (UniqueConstraint("tenant_id", "rule_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    rule_id: Mapped[str] = mapped_column(String(100), index=True)
    name: Mapped[str] = mapped_column(String(120))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=100)
    conditions: Mapped[dict] = mapped_column(JSON, default=dict)
    actions: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class AutomationExecutionModel(Base):
    __tablename__ = "automation_rule_executions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "execution_id"),
        UniqueConstraint("tenant_id", "idempotency_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    execution_id: Mapped[str] = mapped_column(String(100), index=True)
    rule_id: Mapped[str] = mapped_column(String(100), index=True)
    conversation_id: Mapped[str] = mapped_column(String(100), index=True)
    matched: Mapped[bool] = mapped_column(Boolean, index=True)
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    actions_applied: Mapped[list] = mapped_column(JSON, default=list)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class SatisfactionRatingModel(Base):
    __tablename__ = "satisfaction_ratings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "rating_id"),
        UniqueConstraint("tenant_id", "conversation_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    rating_id: Mapped[str] = mapped_column(String(100), index=True)
    site_id: Mapped[str] = mapped_column(String(100), index=True)
    conversation_id: Mapped[str] = mapped_column(String(100), index=True)
    score: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class KnowledgeGapModel(Base):
    __tablename__ = "knowledge_gaps"
    __table_args__ = (UniqueConstraint("tenant_id", "gap_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    gap_id: Mapped[str] = mapped_column(String(100), index=True)
    conversation_id: Mapped[str] = mapped_column(String(100), index=True)
    source: Mapped[str] = mapped_column(String(50))
    category: Mapped[str] = mapped_column(String(50), index=True)
    summary: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), index=True)
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    resolved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
