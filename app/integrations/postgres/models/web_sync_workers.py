from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.integrations.postgres.models.base import Base


class WebSyncWorkerRegistrationModel(Base):
    __tablename__ = "web_sync_worker_registrations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "worker_instance_id",
            name="uq_web_sync_worker_registrations_tenant_worker",
        ),
        ForeignKeyConstraint(
            ("tenant_id",),
            ("tenants.tenant_id",),
            name="fk_web_sync_worker_registrations_tenant",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "capacity_total >= 1",
            name="web_sync_worker_capacity_total_positive",
        ),
        CheckConstraint(
            "capacity_in_use >= 0 AND capacity_in_use <= capacity_total",
            name="web_sync_worker_capacity_in_use_valid",
        ),
        CheckConstraint(
            "started_at <= last_seen_at",
            name="web_sync_worker_started_before_last_seen",
        ),
        CheckConstraint(
            "expires_at > last_seen_at",
            name="web_sync_worker_expiry_after_last_seen",
        ),
        Index(
            "ix_web_sync_worker_registrations_tenant_expiry",
            "tenant_id",
            "expires_at",
        ),
        Index(
            "ix_web_sync_worker_registrations_tenant_health_expiry",
            "tenant_id",
            "health_state",
            "draining",
            "expires_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False)
    worker_instance_id: Mapped[str] = mapped_column(String(200), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    health_state: Mapped[str] = mapped_column(String(30), nullable=False, default="healthy")
    draining: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    runtime_version: Mapped[str] = mapped_column(String(100), nullable=False, default="unknown")
    capacity_total: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    capacity_in_use: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
