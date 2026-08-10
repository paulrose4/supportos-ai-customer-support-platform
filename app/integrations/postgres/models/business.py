from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.integrations.postgres.models.base import Base


class OrderModel(Base):
    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("tenant_id", "order_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    order_id: Mapped[str] = mapped_column(String(100), index=True)
    customer_id: Mapped[str] = mapped_column(String(100), index=True)
    status: Mapped[str] = mapped_column(String(50))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class SupportTicketModel(Base):
    __tablename__ = "support_tickets"
    __table_args__ = (UniqueConstraint("tenant_id", "ticket_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    ticket_id: Mapped[str] = mapped_column(String(100), index=True)
    customer_id: Mapped[str | None] = mapped_column(String(100), index=True, nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="open")
    subject: Mapped[str] = mapped_column(String(500))
    source: Mapped[str] = mapped_column(String(50), default="agent_handoff")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
