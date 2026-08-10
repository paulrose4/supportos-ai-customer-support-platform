from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class WebCrawlPolicy:
    tenant_id: str
    site_id: str
    base_url: str
    seed_urls: tuple[str, ...] = ()
    sitemap_urls: tuple[str, ...] = ()
    allowed_hosts: tuple[str, ...] = ()
    max_pages: int = 500
    max_sitemaps: int = 20
    max_response_bytes: int = 2_000_000
    max_decompressed_response_bytes: int = 4_000_000
    max_compression_ratio: float = 50.0
    request_timeout_seconds: float = 15.0
    crawl_delay_seconds: float = 0.2
    follow_internal_links: bool = True
    respect_robots_txt: bool = True
    user_agent: str = "CompanyProductSupportKnowledgeCrawler/0.1"
    language: str = "en"
    translated_locales: tuple[str, ...] = ()
    enforce_primary_language: bool = False
    authority_level: int = 30
    priority: int = 50
    content_kind: str = "general"
    batch_size: int = 250
    discover_sitemaps: bool = True
    validators: tuple["WebCrawlValidator", ...] = ()


@dataclass(frozen=True, slots=True)
class WebCrawlValidator:
    document_id: str
    version_id: str
    canonical_url: str
    requested_url: str
    final_url: str
    etag: str | None = None
    last_modified: str | None = None
    product_key: str | None = None
    normalized_product_key: str | None = None
    normalization_version: str | None = None


@dataclass(frozen=True, slots=True)
class UnchangedWebDocument:
    document_id: str
    version_id: str
    canonical_url: str
    checked_at: datetime
    etag: str | None = None
    last_modified: str | None = None
    product_key: str | None = None


@dataclass(frozen=True, slots=True)
class SitemapEntry:
    url: str
    last_modified: str | None = None


@dataclass(frozen=True, slots=True)
class SitemapDocument:
    pages: tuple[SitemapEntry, ...] = ()
    nested_sitemaps: tuple[SitemapEntry, ...] = ()


@dataclass(frozen=True, slots=True)
class HtmlContentBlock:
    kind: str
    text: str
    heading_level: int | None = None
    primary: bool = False


@dataclass(frozen=True, slots=True)
class ParsedHtmlPage:
    requested_url: str
    final_url: str
    canonical_url: str
    title: str
    language: str | None
    meta: dict[str, str]
    blocks: tuple[HtmlContentBlock, ...]
    internal_links: tuple[str, ...]
    structured_data: tuple[dict[str, Any], ...]
    parser_profile: str = "selectolax_dom_v1"
    selected_root: str = "document"
    selected_text_characters: int = 0
    title_source: str = "none"
    content_kind: str = "general"


@dataclass(frozen=True, slots=True)
class StructuredWebDocument:
    tenant_id: str
    site_id: str
    document_id: str
    canonical_url: str
    title: str
    language: str
    body: str
    content_hash: str
    category: str
    product: dict[str, Any] | None
    internal_links: tuple[str, ...]
    fetched_at: datetime
    source_metadata: dict[str, Any] = field(default_factory=dict)
    heading_blocks: frozenset[str] | None = None


@dataclass(frozen=True, slots=True)
class WebCrawlResult:
    discovered_count: int
    documents: tuple[StructuredWebDocument, ...]
    excluded_count: int
    failed_count: int
    errors: dict[str, str]
    unchanged_documents: tuple[UnchangedWebDocument, ...] = ()
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class WebKnowledgeSyncReport:
    sync_job_id: str
    discovered_count: int
    document_count: int
    indexed_count: int
    skipped_count: int
    excluded_count: int
    failed_count: int
    errors: dict[str, str]
    product_count: int = 0
    pending_removal_count: int = 0
    expired_count: int = 0
    published: bool = False
    changed_document_count: int = 0
    duplicate_count: int = 0
    http_not_modified_count: int = 0
    duplicate_product_count: int = 0
    duplicate_product_total: int = 0
    duplicate_product_excluded_count: int = 0
    duplicate_product_conflict_warning_count: int = 0
    duplicate_product_unresolved_count: int = 0
    winner_product_count: int = 0
    validators: tuple[WebCrawlValidator, ...] = ()
