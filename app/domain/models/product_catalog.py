from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class ProductDataStatus(StrEnum):
    VALID = "valid"
    PENDING_REMOVAL = "pending_removal"
    EXPIRED = "expired"


class ProductSnapshotStatus(StrEnum):
    STAGING = "staging"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ProductSnapshot:
    tenant_id: str
    site_id: str
    snapshot_id: str
    product_key: str
    canonical_url: str
    name: str
    fetched_at: datetime
    content_hash: str
    source_url: str
    status: ProductDataStatus = ProductDataStatus.VALID
    normalized_product_key: str | None = None
    normalization_version: str | None = None
    sku: str | None = None
    mpn: str | None = None
    brand: str | None = None
    material: str | None = None
    attributes: dict[str, str] = field(default_factory=dict)
    dimensions: dict[str, str] = field(default_factory=dict)
    weight: str | None = None
    price: str | None = None
    currency: str | None = None
    stock_status: str | None = None
    shipping_warehouse: str | None = None
    shipping_regions: tuple[str, ...] = ()
    etag: str | None = None
    last_modified: str | None = None
    missing_count: int = 0


@dataclass(frozen=True, slots=True)
class ProductSnapshotActivation:
    snapshot_id: str
    activated_count: int
    pending_removal_count: int
    expired_count: int
