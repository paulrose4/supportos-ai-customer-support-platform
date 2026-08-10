from datetime import datetime
from hashlib import sha256
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.models.site_web_source import (
    SiteWebDiscoveryMode,
    SiteWebSourceConfig,
    SiteWebSourceValidationStatus,
)
from app.domain.ports.site_web_source import SiteWebSourceConfigVersionConflictError
from app.integrations.postgres.models import AuditEventModel, SupportSiteModel
from app.integrations.postgres.models.site_web_source import SiteWebSourceConfigModel


class PostgreSQLSiteWebSourceConfigStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_site_base_url(self, *, tenant_id: str, site_id: str) -> str | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(SupportSiteModel.base_url).where(
                    SupportSiteModel.tenant_id == tenant_id,
                    SupportSiteModel.site_id == site_id,
                )
            )

    async def list_active_verified_site_base_urls(
        self,
        *,
        tenant_id: str,
    ) -> tuple[str, ...]:
        async with self._session_factory() as session:
            values = await session.scalars(
                select(SupportSiteModel.base_url)
                .where(
                    SupportSiteModel.tenant_id == tenant_id,
                    SupportSiteModel.status == "active",
                    SupportSiteModel.verification_status == "verified",
                )
                .order_by(SupportSiteModel.site_id)
            )
            return tuple(str(value) for value in values)

    async def get_config(
        self,
        *,
        tenant_id: str,
        site_id: str,
    ) -> SiteWebSourceConfig | None:
        async with self._session_factory() as session:
            model = await session.scalar(
                select(SiteWebSourceConfigModel).where(
                    SiteWebSourceConfigModel.tenant_id == tenant_id,
                    SiteWebSourceConfigModel.site_id == site_id,
                )
            )
        return None if model is None else _to_domain(model)

    async def put_config(
        self,
        *,
        tenant_id: str,
        site_id: str,
        expected_base_url: str,
        expected_config_version: int,
        discovery_mode: SiteWebDiscoveryMode,
        explicit_sitemap_urls: tuple[str, ...],
        actor_subject_id: str,
        correlation_id: str,
        changed_at: datetime,
    ) -> SiteWebSourceConfig | None:
        async with self._session_factory.begin() as session:
            site = await session.scalar(
                select(SupportSiteModel)
                .where(
                    SupportSiteModel.tenant_id == tenant_id,
                    SupportSiteModel.site_id == site_id,
                )
                .with_for_update()
            )
            if site is None:
                return None
            if site.base_url != expected_base_url:
                raise ValueError("site base URL changed while the configuration was being saved")
            model = await session.scalar(
                select(SiteWebSourceConfigModel)
                .where(
                    SiteWebSourceConfigModel.tenant_id == tenant_id,
                    SiteWebSourceConfigModel.site_id == site_id,
                )
                .with_for_update()
            )
            urls = list(explicit_sitemap_urls)
            if (
                model is not None
                and model.discovery_mode == discovery_mode.value
                and list(model.explicit_sitemap_urls or ()) == urls
            ):
                return _to_domain(model)
            current_version = 0 if model is None else model.config_version
            if current_version != expected_config_version:
                raise SiteWebSourceConfigVersionConflictError(
                    "site web source configuration version is stale"
                )
            previous = None if model is None else _audit_projection(model)
            if model is None:
                model = SiteWebSourceConfigModel(
                    tenant_id=tenant_id,
                    site_id=site_id,
                    discovery_mode=discovery_mode.value,
                    explicit_sitemap_urls=urls,
                    config_version=1,
                    validation_status=SiteWebSourceValidationStatus.UNVALIDATED.value,
                    validated_at=None,
                    updated_by=actor_subject_id,
                    updated_at=changed_at,
                )
                session.add(model)
            else:
                model.discovery_mode = discovery_mode.value
                model.explicit_sitemap_urls = urls
                model.config_version += 1
                model.validation_status = SiteWebSourceValidationStatus.UNVALIDATED.value
                model.validated_at = None
                model.updated_by = actor_subject_id
                model.updated_at = changed_at
            session.add(
                AuditEventModel(
                    tenant_id=tenant_id,
                    event_id=str(uuid4()),
                    event_type="site_web_source_config.updated",
                    actor_subject_id=actor_subject_id,
                    correlation_id=correlation_id,
                    resource_type="site_web_source_config",
                    resource_id=site_id,
                    details={
                        "previous": previous,
                        "current": _audit_projection(model),
                    },
                    created_at=changed_at,
                )
            )
            await session.flush()
            return _to_domain(model)

    async def mark_validation_status(
        self,
        *,
        tenant_id: str,
        site_id: str,
        expected_config_version: int,
        validation_status: SiteWebSourceValidationStatus,
        validated_at: datetime | None,
        actor_subject_id: str,
        correlation_id: str,
        changed_at: datetime,
    ) -> SiteWebSourceConfig | None:
        async with self._session_factory.begin() as session:
            model = await session.scalar(
                select(SiteWebSourceConfigModel)
                .where(
                    SiteWebSourceConfigModel.tenant_id == tenant_id,
                    SiteWebSourceConfigModel.site_id == site_id,
                    SiteWebSourceConfigModel.config_version == expected_config_version,
                )
                .with_for_update()
            )
            if model is None:
                return None
            if (
                model.validation_status == validation_status.value
                and model.validated_at == validated_at
            ):
                return _to_domain(model)
            model.validation_status = validation_status.value
            model.validated_at = validated_at
            session.add(
                AuditEventModel(
                    tenant_id=tenant_id,
                    event_id=str(uuid4()),
                    event_type="site_web_source_config.validation_updated",
                    actor_subject_id=actor_subject_id,
                    correlation_id=correlation_id,
                    resource_type="site_web_source_config",
                    resource_id=site_id,
                    details={
                        "config_version": model.config_version,
                        "validation_status": validation_status.value,
                    },
                    created_at=changed_at,
                )
            )
            await session.flush()
            return _to_domain(model)


def _to_domain(model: SiteWebSourceConfigModel) -> SiteWebSourceConfig:
    return SiteWebSourceConfig(
        tenant_id=model.tenant_id,
        site_id=model.site_id,
        discovery_mode=SiteWebDiscoveryMode(model.discovery_mode),
        explicit_sitemap_urls=tuple(str(value) for value in model.explicit_sitemap_urls or ()),
        config_version=model.config_version,
        validation_status=SiteWebSourceValidationStatus(model.validation_status),
        validated_at=model.validated_at,
        updated_by=model.updated_by,
        updated_at=model.updated_at,
    )


def _audit_projection(model: SiteWebSourceConfigModel) -> dict[str, object]:
    urls = tuple(str(value) for value in model.explicit_sitemap_urls or ())
    return {
        "discovery_mode": model.discovery_mode,
        "explicit_sitemap_urls": [_redacted_url(value) for value in urls],
        "explicit_sitemap_url_hashes": [
            sha256(value.encode("utf-8")).hexdigest() for value in urls
        ],
        "config_version": model.config_version,
    }


def _redacted_url(url: str) -> str:
    parsed = urlsplit(url)
    query = "[redacted]" if parsed.query else ""
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))
