from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.models.widget_asset import (
    WidgetAsset,
    WidgetAssetPurpose,
    WidgetAssetStatus,
)
from app.integrations.postgres.models import AuditEventModel, SupportSiteModel
from app.integrations.postgres.models.widget_assets import WidgetAssetModel


class PostgreSQLWidgetAssetRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def site_exists(self, *, tenant_id: str, site_id: str) -> bool:
        async with self._session_factory() as session:
            existing = await session.scalar(
                select(SupportSiteModel.id).where(
                    SupportSiteModel.tenant_id == tenant_id,
                    SupportSiteModel.site_id == site_id,
                )
            )
        return existing is not None

    async def get_asset(self, *, tenant_id: str, site_id: str, asset_id: str) -> WidgetAsset | None:
        async with self._session_factory() as session:
            model = await session.scalar(
                select(WidgetAssetModel).where(
                    WidgetAssetModel.tenant_id == tenant_id,
                    WidgetAssetModel.site_id == site_id,
                    WidgetAssetModel.asset_id == asset_id,
                )
            )
        return _asset(model) if model is not None else None

    async def get_by_idempotency_key(
        self, *, tenant_id: str, idempotency_key: str
    ) -> WidgetAsset | None:
        async with self._session_factory() as session:
            model = await session.scalar(
                select(WidgetAssetModel).where(
                    WidgetAssetModel.tenant_id == tenant_id,
                    WidgetAssetModel.idempotency_key == idempotency_key,
                )
            )
        return _asset(model) if model is not None else None

    async def list_assets(
        self, *, tenant_id: str, site_id: str, limit: int
    ) -> tuple[WidgetAsset, ...]:
        async with self._session_factory() as session:
            models = await session.scalars(
                select(WidgetAssetModel)
                .where(
                    WidgetAssetModel.tenant_id == tenant_id,
                    WidgetAssetModel.site_id == site_id,
                    WidgetAssetModel.status == WidgetAssetStatus.ACTIVE.value,
                )
                .order_by(WidgetAssetModel.created_at.desc())
                .limit(limit)
            )
            return tuple(_asset(model) for model in models)

    async def save_asset(
        self,
        *,
        asset: WidgetAsset,
        actor_subject_id: str,
        correlation_id: str,
        idempotency_key: str,
    ) -> WidgetAsset:
        async with self._session_factory.begin() as session:
            existing = await session.scalar(
                select(WidgetAssetModel).where(
                    WidgetAssetModel.tenant_id == asset.tenant_id,
                    WidgetAssetModel.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                replay = _asset(existing)
                if replay.asset_id != asset.asset_id or replay.content_hash != asset.content_hash:
                    raise RuntimeError("idempotency key was already used for another widget image")
                return replay
            site = await session.scalar(
                select(SupportSiteModel.id).where(
                    SupportSiteModel.tenant_id == asset.tenant_id,
                    SupportSiteModel.site_id == asset.site_id,
                )
            )
            if site is None:
                raise LookupError("site was not found")
            model = WidgetAssetModel(
                tenant_id=asset.tenant_id,
                site_id=asset.site_id,
                asset_id=asset.asset_id,
                purpose=asset.purpose.value,
                status=asset.status.value,
                content_hash=asset.content_hash,
                source_content_type=asset.source_content_type,
                source_byte_size=asset.source_byte_size,
                width=asset.width,
                height=asset.height,
                created_by=asset.created_by,
                idempotency_key=idempotency_key,
                created_at=asset.created_at,
                retired_at=asset.retired_at,
            )
            session.add(model)
            session.add(
                AuditEventModel(
                    tenant_id=asset.tenant_id,
                    event_id=asset.asset_id,
                    event_type="widget_asset.uploaded",
                    actor_subject_id=actor_subject_id,
                    correlation_id=correlation_id,
                    resource_type="widget_asset",
                    resource_id=asset.asset_id,
                    details={
                        "site_id": asset.site_id,
                        "purpose": asset.purpose.value,
                        "content_hash": asset.content_hash,
                        "source_byte_size": asset.source_byte_size,
                        "width": asset.width,
                        "height": asset.height,
                    },
                    created_at=asset.created_at,
                )
            )
            await session.flush()
            return _asset(model)


def _asset(model: WidgetAssetModel) -> WidgetAsset:
    return WidgetAsset(
        asset_id=model.asset_id,
        tenant_id=model.tenant_id,
        site_id=model.site_id,
        purpose=WidgetAssetPurpose(model.purpose),
        status=WidgetAssetStatus(model.status),
        content_hash=model.content_hash,
        source_content_type=model.source_content_type,
        source_byte_size=model.source_byte_size,
        width=model.width,
        height=model.height,
        created_by=model.created_by,
        created_at=model.created_at,
        retired_at=model.retired_at,
    )
