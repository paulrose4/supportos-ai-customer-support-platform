from datetime import datetime
from typing import Protocol

from app.domain.models.site_web_source import (
    SiteWebDiscoveryMode,
    SiteWebSourceConfig,
    SiteWebSourceValidationStatus,
)


class SiteWebSourceConfigVersionConflictError(RuntimeError):
    pass


class SiteWebSourceConfigStorePort(Protocol):
    async def get_site_base_url(self, *, tenant_id: str, site_id: str) -> str | None: ...

    async def list_active_verified_site_base_urls(
        self,
        *,
        tenant_id: str,
    ) -> tuple[str, ...]: ...

    async def get_config(
        self,
        *,
        tenant_id: str,
        site_id: str,
    ) -> SiteWebSourceConfig | None: ...

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
    ) -> SiteWebSourceConfig | None: ...

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
    ) -> SiteWebSourceConfig | None: ...
