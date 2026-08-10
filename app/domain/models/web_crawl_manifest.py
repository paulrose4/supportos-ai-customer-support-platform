from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class WebCrawlManifestStatus(StrEnum):
    READY = "ready"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class WebCrawlDiscoveryAttempt:
    url: str
    source: str
    outcome: str
    final_url: str | None = None


@dataclass(frozen=True, slots=True)
class WebCrawlPageState:
    tenant_id: str
    site_id: str
    url: str
    document_id: str
    version_id: str
    canonical_url: str
    final_url: str
    etag: str | None = None
    last_modified: str | None = None
    product_key: str | None = None
    normalized_product_key: str | None = None
    normalization_version: str | None = None
    artifact_status: str = "published"
    validated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class WebCrawlManifestItem:
    url: str
    source_sitemap_url: str
    content_kind: str = "general"
    last_modified: str | None = None
    document_id: str | None = None
    version_id: str | None = None
    canonical_url: str | None = None
    final_url: str | None = None
    etag: str | None = None
    response_last_modified: str | None = None
    product_key: str | None = None
    normalized_product_key: str | None = None
    normalization_version: str | None = None
    artifact_status: str = "unknown"
    validated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class WebCrawlManifest:
    tenant_id: str
    site_id: str
    manifest_id: str
    base_url: str
    root_sitemap_url: str
    primary_language: str
    translation_provider: str
    status: WebCrawlManifestStatus
    fingerprint: str
    primary_sitemap_urls: tuple[str, ...]
    translated_locales: tuple[str, ...]
    excluded_sitemap_count: int
    excluded_url_count: int
    blocking_reasons: tuple[str, ...]
    created_by: str
    created_at: datetime
    version: int = 1
    policy_version: str = "web-crawl-v1"
    source_config_version: int = 0
    root_sitemap_urls: tuple[str, ...] = ()
    discovery_method: str = "legacy"
    warnings: tuple[str, ...] = ()
    coverage_status: str = "declared_complete"
    discovery_attempts: tuple[WebCrawlDiscoveryAttempt, ...] = ()
    item_count: int | None = None
    item_kind_counts: tuple[tuple[str, int], ...] = ()
    items: tuple[WebCrawlManifestItem, ...] = ()

    @property
    def url_count(self) -> int:
        return len(self.items) if self.item_count is None else self.item_count

    @property
    def content_kind_counts(self) -> dict[str, int]:
        if self.item_kind_counts:
            return dict(self.item_kind_counts)
        counts: dict[str, int] = {}
        for item in self.items:
            counts[item.content_kind] = counts.get(item.content_kind, 0) + 1
        return counts
