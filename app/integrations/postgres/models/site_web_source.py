from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.integrations.postgres.models.base import Base


class SiteWebSourceConfigModel(Base):
    __tablename__ = "site_web_source_configs"
    __table_args__ = (
        ForeignKeyConstraint(
            ("tenant_id", "site_id"),
            ("support_sites.tenant_id", "support_sites.site_id"),
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "discovery_mode IN ('auto', 'hybrid', 'manual')",
            name="ck_site_web_source_configs_discovery_mode",
        ),
        CheckConstraint(
            "validation_status IN ('unvalidated', 'valid', 'invalid')",
            name="ck_site_web_source_configs_validation_status",
        ),
        CheckConstraint(
            "config_version > 0",
            name="ck_site_web_source_configs_config_version",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    site_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    discovery_mode: Mapped[str] = mapped_column(
        String(20),
        default="hybrid",
        server_default="hybrid",
    )
    explicit_sitemap_urls: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        server_default=text("'[]'::json"),
    )
    config_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    validation_status: Mapped[str] = mapped_column(
        String(20),
        default="unvalidated",
        server_default="unvalidated",
    )
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_by: Mapped[str] = mapped_column(String(100))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
