from typing import Protocol

from app.domain.models.widget_asset import WidgetAsset, WidgetAssetVariant


class WidgetAssetRepositoryPort(Protocol):
    async def site_exists(self, *, tenant_id: str, site_id: str) -> bool: ...

    async def get_asset(
        self, *, tenant_id: str, site_id: str, asset_id: str
    ) -> WidgetAsset | None: ...

    async def get_by_idempotency_key(
        self, *, tenant_id: str, idempotency_key: str
    ) -> WidgetAsset | None: ...

    async def list_assets(
        self, *, tenant_id: str, site_id: str, limit: int
    ) -> tuple[WidgetAsset, ...]: ...

    async def save_asset(
        self,
        *,
        asset: WidgetAsset,
        actor_subject_id: str,
        correlation_id: str,
        idempotency_key: str,
    ) -> WidgetAsset: ...


class WidgetAssetStoragePort(Protocol):
    async def put_variants(
        self, *, asset_id: str, variants: tuple[WidgetAssetVariant, ...]
    ) -> None: ...

    async def read_variant(self, *, asset_id: str, size: int) -> bytes | None: ...
