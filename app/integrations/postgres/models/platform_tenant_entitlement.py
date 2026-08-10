from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.integrations.postgres.models.base import Base


class PlatformTenantEntitlementModel(Base):
    """Sanitized control-plane projection of tenant plan and quota metadata."""

    __tablename__ = "platform_tenant_entitlements"

    tenant_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey(
            "tenants.tenant_id",
            name="fk_platform_tenant_entitlements_tenant",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    site_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    plan_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    subscription_status: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    quota_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    subscription_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
