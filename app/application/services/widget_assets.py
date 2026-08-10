from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from uuid import NAMESPACE_URL, UUID, uuid5

from PIL import Image, ImageOps, UnidentifiedImageError

from app.application.dto.widget_assets import (
    ListWidgetAssetsQuery,
    PublicWidgetAssetResult,
    ReadPublicWidgetAssetQuery,
    UploadWidgetAssetCommand,
    WidgetAssetListResult,
    WidgetAssetResult,
)
from app.domain.models.widget_asset import (
    WidgetAsset,
    WidgetAssetPurpose,
    WidgetAssetStatus,
    WidgetAssetVariant,
)
from app.domain.ports.widget_assets import WidgetAssetRepositoryPort, WidgetAssetStoragePort
from app.domain.rules import require_scope

_ALLOWED_CONTENT_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
_VARIANT_SIZES = (64, 128, 256)


class WidgetAssetService:
    def __init__(
        self,
        *,
        repository: WidgetAssetRepositoryPort,
        storage: WidgetAssetStoragePort,
        maximum_upload_bytes: int,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._maximum_upload_bytes = maximum_upload_bytes

    async def upload(self, command: UploadWidgetAssetCommand) -> WidgetAssetResult:
        require_scope(command.principal, "sites:manage")
        site_id = _opaque(command.site_id, "site_id")
        idempotency_key = _idempotency(command.idempotency_key)
        try:
            purpose = WidgetAssetPurpose(command.purpose)
        except ValueError as exc:
            raise ValueError("purpose must be launcher or avatar") from exc
        content_type = command.source_content_type.split(";", 1)[0].strip().casefold()
        if content_type not in _ALLOWED_CONTENT_TYPES:
            raise ValueError("widget images must be PNG, JPEG, or WebP")
        if not command.content or len(command.content) > self._maximum_upload_bytes:
            raise ValueError(
                f"widget image must contain between 1 and {self._maximum_upload_bytes} bytes"
            )
        if not await self._repository.site_exists(
            tenant_id=command.principal.tenant_id,
            site_id=site_id,
        ):
            raise LookupError("site was not found")
        content_hash = sha256(command.content).hexdigest()
        existing = await self._repository.get_by_idempotency_key(
            tenant_id=command.principal.tenant_id, idempotency_key=idempotency_key
        )
        if existing is not None:
            if (
                existing.site_id != site_id
                or existing.purpose is not purpose
                or existing.content_hash != content_hash
            ):
                raise RuntimeError("idempotency key was already used for another widget image")
            return WidgetAssetResult(existing)

        width, height, variants = _normalize_image(command.content, content_type)
        asset_id = str(
            uuid5(
                NAMESPACE_URL,
                "widget-asset:"
                f"{command.principal.tenant_id}:{site_id}:{idempotency_key}:{content_hash}",
            )
        )
        await self._storage.put_variants(asset_id=asset_id, variants=variants)
        asset = WidgetAsset(
            asset_id=asset_id,
            tenant_id=command.principal.tenant_id,
            site_id=site_id,
            purpose=purpose,
            status=WidgetAssetStatus.ACTIVE,
            content_hash=content_hash,
            source_content_type=content_type,
            source_byte_size=len(command.content),
            width=width,
            height=height,
            created_by=command.principal.subject_id,
            created_at=datetime.now(UTC),
        )
        saved = await self._repository.save_asset(
            asset=asset,
            actor_subject_id=command.principal.subject_id,
            correlation_id=command.principal.correlation_id,
            idempotency_key=idempotency_key,
        )
        return WidgetAssetResult(saved)

    async def list_assets(self, query: ListWidgetAssetsQuery) -> WidgetAssetListResult:
        require_scope(query.principal, "sites:manage")
        if not 1 <= query.limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        assets = await self._repository.list_assets(
            tenant_id=query.principal.tenant_id,
            site_id=_opaque(query.site_id, "site_id"),
            limit=query.limit,
        )
        return WidgetAssetListResult(assets)

    async def read_public(self, query: ReadPublicWidgetAssetQuery) -> PublicWidgetAssetResult:
        try:
            normalized_id = str(UUID(query.asset_id))
        except ValueError as exc:
            raise LookupError("widget image was not found") from exc
        if query.size not in _VARIANT_SIZES:
            raise ValueError("widget image size must be 64, 128, or 256")
        content = await self._storage.read_variant(asset_id=normalized_id, size=query.size)
        if content is None:
            raise LookupError("widget image was not found")
        return PublicWidgetAssetResult(content)


def _normalize_image(
    content: bytes, declared_content_type: str
) -> tuple[int, int, tuple[WidgetAssetVariant, ...]]:
    try:
        with Image.open(BytesIO(content)) as probe:
            probe.verify()
        with Image.open(BytesIO(content)) as source:
            actual_format = str(source.format or "").upper()
            expected_formats = {
                "image/jpeg": {"JPEG"},
                "image/png": {"PNG"},
                "image/webp": {"WEBP"},
            }[declared_content_type]
            if actual_format not in expected_formats:
                raise ValueError("widget image content does not match its content type")
            width, height = source.size
            if width < 32 or height < 32 or width > 4096 or height > 4096:
                raise ValueError("widget image dimensions must be between 32 and 4096 pixels")
            normalized = ImageOps.exif_transpose(source).convert("RGBA")
            width, height = normalized.size
            variants = []
            for size in _VARIANT_SIZES:
                variant = normalized.copy()
                variant.thumbnail((size, size), Image.Resampling.LANCZOS)
                output = BytesIO()
                variant.save(output, format="WEBP", lossless=True, method=6)
                variants.append(WidgetAssetVariant(size=size, content=output.getvalue()))
            return width, height, tuple(variants)
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as exc:
        raise ValueError("widget image is invalid or unsafe") from exc


def _opaque(value: str, name: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 100:
        raise ValueError(f"{name} must be a bounded opaque identifier")
    return normalized


def _idempotency(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 200:
        raise ValueError("idempotency key must contain between 1 and 200 characters")
    return normalized
