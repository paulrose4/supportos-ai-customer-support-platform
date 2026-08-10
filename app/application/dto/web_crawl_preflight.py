from dataclasses import dataclass

from app.domain.models import AuthenticatedPrincipal
from app.domain.models.web_crawl_manifest import WebCrawlManifest


@dataclass(frozen=True, slots=True)
class RunWebCrawlPreflightCommand:
    principal: AuthenticatedPrincipal
    site_id: str
    base_url: str
    primary_language: str
    translation_provider: str = "gtranslate"
    discovery_mode: str = "hybrid"
    explicit_sitemap_urls: tuple[str, ...] = ()
    allowed_sitemap_origins: tuple[str, ...] = ()
    source_config_version: int = 0


@dataclass(frozen=True, slots=True)
class GetLatestWebCrawlManifestQuery:
    principal: AuthenticatedPrincipal
    site_id: str


@dataclass(frozen=True, slots=True)
class WebCrawlPreflightRuntimePolicy:
    max_sitemaps: int
    max_response_bytes: int
    request_timeout_seconds: float
    max_decompressed_response_bytes: int = 20_000_000
    max_compression_ratio: float = 50.0
    user_agent: str = "CompanyProductSupportKnowledgeCrawler/0.1"
    max_urls: int = 50_000


@dataclass(frozen=True, slots=True)
class WebCrawlPreflightResult:
    manifest: WebCrawlManifest
