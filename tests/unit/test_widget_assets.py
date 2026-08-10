from datetime import UTC, datetime
from io import BytesIO

import pytest
from PIL import Image

from app.application.dto.widget_assets import (
    ListWidgetAssetsQuery,
    ReadPublicWidgetAssetQuery,
    UploadWidgetAssetCommand,
)
from app.application.services.widget_assets import WidgetAssetService
from app.domain.models import AuthenticatedPrincipal
from app.domain.models.widget_asset import WidgetAsset, WidgetAssetVariant


class InMemoryWidgetAssetRepository:
    def __init__(self) -> None:
        self.assets: dict[str, WidgetAsset] = {}
        self.idempotency: dict[tuple[str, str], WidgetAsset] = {}
        self.sites = {("tenant-a", "site-a"), ("tenant-a", "site-b")}

    async def site_exists(self, *, tenant_id: str, site_id: str) -> bool:
        return (tenant_id, site_id) in self.sites

    async def get_asset(self, *, tenant_id: str, site_id: str, asset_id: str) -> WidgetAsset | None:
        asset = self.assets.get(asset_id)
        if asset is None or (asset.tenant_id, asset.site_id) != (tenant_id, site_id):
            return None
        return asset

    async def get_by_idempotency_key(
        self, *, tenant_id: str, idempotency_key: str
    ) -> WidgetAsset | None:
        return self.idempotency.get((tenant_id, idempotency_key))

    async def list_assets(
        self, *, tenant_id: str, site_id: str, limit: int
    ) -> tuple[WidgetAsset, ...]:
        return tuple(
            asset
            for asset in self.assets.values()
            if (asset.tenant_id, asset.site_id) == (tenant_id, site_id)
        )[:limit]

    async def save_asset(
        self,
        *,
        asset: WidgetAsset,
        actor_subject_id: str,
        correlation_id: str,
        idempotency_key: str,
    ) -> WidgetAsset:
        del actor_subject_id, correlation_id
        key = (asset.tenant_id, idempotency_key)
        existing = self.idempotency.get(key)
        if existing is not None:
            if existing.asset_id != asset.asset_id:
                raise RuntimeError("idempotency key collision")
            return existing
        self.assets[asset.asset_id] = asset
        self.idempotency[key] = asset
        return asset


class InMemoryWidgetAssetStorage:
    def __init__(self) -> None:
        self.variants: dict[tuple[str, int], bytes] = {}
        self.put_calls = 0

    async def put_variants(
        self, *, asset_id: str, variants: tuple[WidgetAssetVariant, ...]
    ) -> None:
        self.put_calls += 1
        for variant in variants:
            self.variants[(asset_id, variant.size)] = variant.content

    async def read_variant(self, *, asset_id: str, size: int) -> bytes | None:
        return self.variants.get((asset_id, size))


def _principal(*, tenant_id: str = "tenant-a") -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        subject_id="operator-a",
        tenant_id=tenant_id,
        roles=frozenset({"support_manager"}),
        scopes=frozenset({"sites:manage"}),
        authentication_method="test",
        authenticated_at=datetime.now(UTC),
        correlation_id="correlation-a",
    )


def _image_bytes(*, image_format: str = "PNG", color: str = "#2563eb") -> bytes:
    output = BytesIO()
    Image.new("RGBA", (96, 64), color).save(output, format=image_format)
    return output.getvalue()


def _service() -> tuple[
    WidgetAssetService, InMemoryWidgetAssetRepository, InMemoryWidgetAssetStorage
]:
    repository = InMemoryWidgetAssetRepository()
    storage = InMemoryWidgetAssetStorage()
    return (
        WidgetAssetService(
            repository=repository,
            storage=storage,
            maximum_upload_bytes=2_000_000,
        ),
        repository,
        storage,
    )


async def test_widget_asset_upload_normalizes_variants_and_replays_idempotently() -> None:
    service, _, storage = _service()
    command = UploadWidgetAssetCommand(
        principal=_principal(),
        site_id="site-a",
        purpose="launcher",
        source_content_type="image/png",
        content=_image_bytes(),
        idempotency_key="upload-a",
    )

    first = await service.upload(command)
    replay = await service.upload(command)

    assert replay.asset == first.asset
    assert storage.put_calls == 1
    assert set(size for asset_id, size in storage.variants if asset_id == first.asset.asset_id) == {
        64,
        128,
        256,
    }
    public = await service.read_public(ReadPublicWidgetAssetQuery(first.asset.asset_id, 128))
    with Image.open(BytesIO(public.content)) as normalized:
        assert normalized.format == "WEBP"
        assert normalized.size == (96, 64)


async def test_widget_asset_id_changes_with_content_for_concurrent_write_safety() -> None:
    service, repository, storage = _service()
    first = UploadWidgetAssetCommand(
        _principal(), "site-a", "launcher", "image/png", _image_bytes(), "same-key"
    )
    second = UploadWidgetAssetCommand(
        _principal(),
        "site-a",
        "launcher",
        "image/png",
        _image_bytes(color="#dc2626"),
        "same-key",
    )
    await service.upload(first)

    with pytest.raises(RuntimeError, match="another widget image"):
        await service.upload(second)

    assert len(repository.assets) == 1
    assert storage.put_calls == 1

    other_service, _, _ = _service()
    other = await other_service.upload(
        UploadWidgetAssetCommand(
            _principal(),
            "site-a",
            "launcher",
            "image/png",
            _image_bytes(color="#dc2626"),
            "same-key",
        )
    )
    assert other.asset.asset_id != next(iter(repository.assets))


async def test_widget_asset_rejects_mime_mismatch_and_keeps_site_lists_isolated() -> None:
    service, _, _ = _service()
    with pytest.raises(ValueError, match="does not match"):
        await service.upload(
            UploadWidgetAssetCommand(
                _principal(), "site-a", "avatar", "image/jpeg", _image_bytes(), "bad-mime"
            )
        )
    created = await service.upload(
        UploadWidgetAssetCommand(
            _principal(), "site-a", "avatar", "image/png", _image_bytes(), "valid"
        )
    )

    own = await service.list_assets(ListWidgetAssetsQuery(_principal(), "site-a", 100))
    other_site = await service.list_assets(ListWidgetAssetsQuery(_principal(), "site-b", 100))
    other_tenant = await service.list_assets(
        ListWidgetAssetsQuery(_principal(tenant_id="tenant-b"), "site-a", 100)
    )

    assert own.assets == (created.asset,)
    assert other_site.assets == ()
    assert other_tenant.assets == ()


async def test_widget_asset_rejects_unsupported_dimensions_and_public_sizes() -> None:
    service, _, _ = _service()
    tiny = BytesIO()
    Image.new("RGB", (16, 16), "white").save(tiny, format="PNG")
    with pytest.raises(ValueError, match="dimensions"):
        await service.upload(
            UploadWidgetAssetCommand(
                _principal(), "site-a", "launcher", "image/png", tiny.getvalue(), "tiny"
            )
        )
    with pytest.raises(ValueError, match="64, 128, or 256"):
        await service.read_public(
            ReadPublicWidgetAssetQuery("00000000-0000-0000-0000-000000000000", 65)
        )


async def test_widget_asset_rejects_unknown_site_before_writing_storage() -> None:
    service, _, storage = _service()

    with pytest.raises(LookupError, match="site was not found"):
        await service.upload(
            UploadWidgetAssetCommand(
                _principal(),
                "missing-site",
                "launcher",
                "image/png",
                _image_bytes(),
                "unknown-site",
            )
        )

    assert storage.put_calls == 0
