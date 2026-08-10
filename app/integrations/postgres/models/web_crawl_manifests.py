from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.integrations.postgres.models.base import Base


class WebCrawlManifestModel(Base):
    __tablename__ = "web_crawl_manifests"
    __table_args__ = (
        UniqueConstraint("tenant_id", "site_id", "manifest_id"),
        CheckConstraint(
            "source_config_version >= 0",
            name="ck_web_crawl_manifests_source_config_version",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "site_id"),
            ("support_sites.tenant_id", "support_sites.site_id"),
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    site_id: Mapped[str] = mapped_column(String(100), index=True)
    manifest_id: Mapped[str] = mapped_column(String(100), index=True)
    base_url: Mapped[str] = mapped_column(Text)
    root_sitemap_url: Mapped[str] = mapped_column(Text)
    root_sitemap_urls: Mapped[list[str]] = mapped_column(JSON, default=list)
    discovery_method: Mapped[str] = mapped_column(String(30), default="legacy")
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    coverage_status: Mapped[str] = mapped_column(String(30), default="declared_complete")
    discovery_attempts: Mapped[list[dict[str, str | None]]] = mapped_column(JSON, default=list)
    primary_language: Mapped[str] = mapped_column(String(20))
    translation_provider: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(
        Integer,
        server_default=text("nextval('web_crawl_manifest_version_seq')"),
    )
    policy_version: Mapped[str] = mapped_column(String(100), default="web-crawl-v1")
    source_config_version: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
    )
    url_count: Mapped[int] = mapped_column(Integer, default=0)
    content_kind_counts: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    primary_sitemap_urls: Mapped[list[str]] = mapped_column(JSON, default=list)
    translated_locales: Mapped[list[str]] = mapped_column(JSON, default=list)
    excluded_sitemap_count: Mapped[int] = mapped_column(Integer, default=0)
    excluded_url_count: Mapped[int] = mapped_column(Integer, default=0)
    blocking_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(String(100), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )


class WebCrawlManifestItemModel(Base):
    __tablename__ = "web_crawl_manifest_items"
    __table_args__ = (
        UniqueConstraint("tenant_id", "site_id", "manifest_id", "url"),
        ForeignKeyConstraint(
            ("tenant_id", "site_id", "manifest_id"),
            (
                "web_crawl_manifests.tenant_id",
                "web_crawl_manifests.site_id",
                "web_crawl_manifests.manifest_id",
            ),
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    site_id: Mapped[str] = mapped_column(String(100), index=True)
    manifest_id: Mapped[str] = mapped_column(String(100), index=True)
    url: Mapped[str] = mapped_column(Text)
    source_sitemap_url: Mapped[str] = mapped_column(Text)
    content_kind: Mapped[str] = mapped_column(String(30), default="general", index=True)
    last_modified: Mapped[str | None] = mapped_column(String(200), nullable=True)
    document_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    version_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    canonical_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    etag: Mapped[str | None] = mapped_column(String(500), nullable=True)
    response_last_modified: Mapped[str | None] = mapped_column(String(200), nullable=True)
    product_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    normalized_product_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    normalization_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    artifact_status: Mapped[str] = mapped_column(String(30), default="unknown", index=True)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WebCrawlPageStateModel(Base):
    __tablename__ = "web_crawl_page_states"
    __table_args__ = (
        UniqueConstraint("tenant_id", "site_id", "url"),
        ForeignKeyConstraint(
            ("tenant_id", "site_id"),
            ("support_sites.tenant_id", "support_sites.site_id"),
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    site_id: Mapped[str] = mapped_column(String(100), index=True)
    url: Mapped[str] = mapped_column(Text)
    document_id: Mapped[str] = mapped_column(String(100))
    version_id: Mapped[str] = mapped_column(String(100))
    canonical_url: Mapped[str] = mapped_column(Text)
    final_url: Mapped[str] = mapped_column(Text)
    etag: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_modified: Mapped[str | None] = mapped_column(String(200), nullable=True)
    product_key: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    normalized_product_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    normalization_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    artifact_status: Mapped[str] = mapped_column(String(30), default="published", index=True)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )
