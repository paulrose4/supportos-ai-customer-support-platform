from datetime import datetime

from sqlalchemy import DateTime, ForeignKeyConstraint, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.integrations.postgres.models.base import Base


class PlatformSiteDirectoryModel(Base):
    """Control-plane projection of tenant-owned site metadata.

    This table deliberately excludes credentials and tenant business data. It is
    the read surface for platform administrators, while support_sites remains
    authoritative for tenant operations.
    """

    __tablename__ = "platform_site_directory"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "site_id",
            name="uq_platform_site_directory_tenant_site",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "site_id"),
            ("support_sites.tenant_id", "support_sites.site_id"),
            name="fk_platform_site_directory_support_site",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    site_id: Mapped[str] = mapped_column(String(100), index=True)
    name: Mapped[str] = mapped_column(String(200))
    base_url: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(30), index=True)
    verification_status: Mapped[str] = mapped_column(String(30), index=True)
    verification_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    knowledge_publication_state: Mapped[str] = mapped_column(String(30), index=True)
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
