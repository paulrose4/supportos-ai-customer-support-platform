from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKeyConstraint, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.integrations.postgres.models.base import Base


class ProductCatalogSnapshotModel(Base):
    __tablename__ = "product_catalog_snapshots"
    __table_args__ = (UniqueConstraint("tenant_id", "site_id", "snapshot_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    site_id: Mapped[str] = mapped_column(String(100), index=True)
    snapshot_id: Mapped[str] = mapped_column(String(100), index=True)
    sync_job_id: Mapped[str] = mapped_column(String(100), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    staged_count: Mapped[int] = mapped_column(Integer, default=0)
    active_count: Mapped[int] = mapped_column(Integer, default=0)
    pending_removal_count: Mapped[int] = mapped_column(Integer, default=0)
    expired_count: Mapped[int] = mapped_column(Integer, default=0)
    error_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProductFactSnapshotModel(Base):
    __tablename__ = "product_fact_snapshots"
    __table_args__ = (
        UniqueConstraint("tenant_id", "site_id", "snapshot_id", "product_key"),
        UniqueConstraint(
            "tenant_id",
            "site_id",
            "snapshot_id",
            "normalized_product_key",
            name="uq_product_fact_snapshot_normalized_identity",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "site_id", "snapshot_id"),
            (
                "product_catalog_snapshots.tenant_id",
                "product_catalog_snapshots.site_id",
                "product_catalog_snapshots.snapshot_id",
            ),
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    site_id: Mapped[str] = mapped_column(String(100), index=True)
    snapshot_id: Mapped[str] = mapped_column(String(100), index=True)
    product_key: Mapped[str] = mapped_column(String(500), index=True)
    normalized_product_key: Mapped[str] = mapped_column(String(500), index=True)
    normalization_version: Mapped[str] = mapped_column(String(100))
    sku: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    mpn: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(500))
    canonical_url: Mapped[str] = mapped_column(Text)
    canonical_path: Mapped[str] = mapped_column(String(500), index=True)
    brand: Mapped[str | None] = mapped_column(String(300), nullable=True)
    material: Mapped[str | None] = mapped_column(Text, nullable=True)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    dimensions: Mapped[dict] = mapped_column(JSON, default=dict)
    weight: Mapped[str | None] = mapped_column(String(200), nullable=True)
    price: Mapped[str | None] = mapped_column(String(100), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(20), nullable=True)
    stock_status: Mapped[str | None] = mapped_column(String(200), nullable=True)
    shipping_warehouse: Mapped[str | None] = mapped_column(String(300), nullable=True)
    shipping_regions: Mapped[list] = mapped_column(JSON, default=list)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    source_url: Mapped[str] = mapped_column(Text)
    etag: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_modified: Mapped[str | None] = mapped_column(String(500), nullable=True)
    data_status: Mapped[str] = mapped_column(String(30), index=True)
    missing_count: Mapped[int] = mapped_column(Integer, default=0)
