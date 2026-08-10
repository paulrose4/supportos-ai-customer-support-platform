from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.domain.models.product_catalog import ProductSnapshot, ProductSnapshotActivation


class ProductIdentityConflictError(ValueError):
    def __init__(
        self,
        *,
        product_key: str,
        fields: Sequence[str],
        existing_url: str,
        candidate_url: str,
    ) -> None:
        self.product_key = product_key
        self.fields = tuple(fields)
        self.existing_url = existing_url
        self.candidate_url = candidate_url
        detail = ",".join(self.fields)
        super().__init__(f"product_identity_conflict:{product_key}:{detail}")


@dataclass(frozen=True, slots=True)
class ProductLookup:
    tenant_id: str
    site_id: str | None = None
    sku: str | None = None
    mpn: str | None = None
    canonical_url: str | None = None
    page_path: str | None = None


@dataclass(frozen=True, slots=True)
class ActiveProductCatalogSummary:
    snapshot_id: str | None = None
    product_count: int = 0
    completed_at: datetime | None = None


class ProductCatalogPort(Protocol):
    async def begin_snapshot(
        self,
        *,
        tenant_id: str,
        site_id: str,
        snapshot_id: str,
        sync_job_id: str,
        started_at: datetime,
    ) -> None: ...

    async def stage_products(
        self,
        *,
        tenant_id: str,
        site_id: str,
        snapshot_id: str,
        products: Sequence[ProductSnapshot],
    ) -> None: ...

    async def discard_staged_product(
        self,
        *,
        tenant_id: str,
        site_id: str,
        snapshot_id: str,
        normalized_product_key: str,
        canonical_url: str,
    ) -> None: ...

    async def activate_snapshot(
        self,
        *,
        tenant_id: str,
        site_id: str,
        snapshot_id: str,
        completed_at: datetime,
        missing_confirmation_threshold: int = 2,
    ) -> ProductSnapshotActivation: ...

    async def restore_snapshot(
        self,
        *,
        tenant_id: str,
        site_id: str,
        failed_snapshot_id: str,
        previous_snapshot_id: str | None,
        restored_at: datetime,
        error_summary: dict[str, str],
    ) -> None: ...

    async def fail_snapshot(
        self,
        *,
        tenant_id: str,
        site_id: str,
        snapshot_id: str,
        error_summary: dict[str, str],
    ) -> None: ...

    async def count_staged_products(
        self,
        *,
        tenant_id: str,
        site_id: str,
        snapshot_id: str,
    ) -> int: ...

    async def find_exact(self, lookup: ProductLookup) -> ProductSnapshot | None: ...

    async def list_active_products(
        self,
        *,
        tenant_id: str,
        site_id: str | None = None,
        limit: int = 200,
    ) -> tuple[ProductSnapshot, ...]: ...

    async def list_active_products_by_keys(
        self,
        *,
        tenant_id: str,
        site_id: str | None,
        product_keys: Sequence[str],
    ) -> tuple[ProductSnapshot, ...]: ...

    async def get_active_summary(
        self,
        *,
        tenant_id: str,
        site_id: str,
    ) -> ActiveProductCatalogSummary: ...
