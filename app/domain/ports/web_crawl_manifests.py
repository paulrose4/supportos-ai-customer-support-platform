from typing import Protocol

from app.domain.models.web_crawl_manifest import (
    WebCrawlManifest,
    WebCrawlManifestItem,
    WebCrawlPageState,
)


class WebCrawlManifestStorePort(Protocol):
    async def save(self, manifest: WebCrawlManifest) -> WebCrawlManifest: ...

    async def get(
        self,
        *,
        tenant_id: str,
        site_id: str,
        manifest_id: str,
    ) -> WebCrawlManifest | None: ...

    async def get_metadata(
        self,
        *,
        tenant_id: str,
        site_id: str,
        manifest_id: str,
    ) -> WebCrawlManifest | None: ...

    async def get_latest(
        self,
        *,
        tenant_id: str,
        site_id: str,
    ) -> WebCrawlManifest | None: ...

    async def list_items(
        self,
        *,
        tenant_id: str,
        site_id: str,
        manifest_id: str,
        offset: int,
        limit: int,
        deterministic_sample: bool = False,
    ) -> tuple[WebCrawlManifestItem, ...]: ...

    async def list_page_states(
        self,
        *,
        tenant_id: str,
        site_id: str,
    ) -> tuple[WebCrawlPageState, ...]: ...

    async def replace_page_states(
        self,
        *,
        tenant_id: str,
        site_id: str,
        states: tuple[WebCrawlPageState, ...],
    ) -> None: ...
