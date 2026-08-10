from dataclasses import dataclass
from typing import Protocol

from app.domain.models.web_crawl_manifest import (
    WebCrawlDiscoveryAttempt,
    WebCrawlManifestItem,
)


@dataclass(frozen=True, slots=True)
class WebCrawlInspectionRequest:
    base_url: str
    primary_language: str
    translation_provider: str
    max_sitemaps: int
    max_response_bytes: int
    request_timeout_seconds: float
    user_agent: str
    max_decompressed_response_bytes: int = 20_000_000
    max_compression_ratio: float = 50.0
    discovery_mode: str = "hybrid"
    explicit_sitemap_urls: tuple[str, ...] = ()
    allowed_sitemap_origins: tuple[str, ...] = ()
    previous_sitemap_urls: tuple[str, ...] = ()
    max_urls: int = 50_000


@dataclass(frozen=True, slots=True)
class WebCrawlInspection:
    root_sitemap_url: str
    primary_sitemap_urls: tuple[str, ...]
    translated_locales: tuple[str, ...]
    excluded_sitemap_count: int
    excluded_url_count: int
    blocking_reasons: tuple[str, ...]
    items: tuple[WebCrawlManifestItem, ...]
    root_sitemap_urls: tuple[str, ...] = ()
    discovery_method: str = "legacy"
    warnings: tuple[str, ...] = ()
    coverage_status: str = "declared_complete"
    discovery_attempts: tuple[WebCrawlDiscoveryAttempt, ...] = ()


class WebCrawlPreflightInspectorPort(Protocol):
    async def inspect(self, request: WebCrawlInspectionRequest) -> WebCrawlInspection: ...
