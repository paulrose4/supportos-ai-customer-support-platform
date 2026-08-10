import asyncio
from collections import deque
from dataclasses import replace
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

from app.domain.ports.web_knowledge import (
    ResponseBudgetExceededError,
    UnsupportedWebContentTypeError,
    WebFetchRequest,
    WebPageFetcherPort,
)
from app.knowledge.web.canonicalizer import canonicalize_url, host_for_url
from app.knowledge.web.content_extractor import StructuredContentExtractor
from app.knowledge.web.deduplicator import WebDocumentDeduplicator
from app.knowledge.web.errors import RetryableWebFetchError, UnusableWebContentError
from app.knowledge.web.html_parser import HtmlKnowledgeParser
from app.knowledge.web.language_scope import is_translated_url, language_matches_primary
from app.knowledge.web.models import (
    SitemapEntry,
    UnchangedWebDocument,
    WebCrawlPolicy,
    WebCrawlResult,
    WebCrawlValidator,
)
from app.knowledge.web.product_extractor import ProductExtractor
from app.knowledge.web.sitemap import SitemapDiscovery
from app.knowledge.web.url_scope import is_excluded_web_url


class WebsiteKnowledgeCrawler:
    def __init__(
        self,
        *,
        fetcher: WebPageFetcherPort,
        sitemap_discovery: SitemapDiscovery | None = None,
        html_parser: HtmlKnowledgeParser | None = None,
        content_extractor: StructuredContentExtractor | None = None,
        product_extractor: ProductExtractor | None = None,
        robots_cache_ttl_seconds: float = 60.0,
        domain_concurrency: int = 2,
    ) -> None:
        self._fetcher = fetcher
        self._sitemap_discovery = sitemap_discovery or SitemapDiscovery(fetcher)
        self._html_parser = html_parser or HtmlKnowledgeParser()
        self._content_extractor = content_extractor or StructuredContentExtractor()
        self._product_extractor = product_extractor or ProductExtractor()
        self._robots_cache_ttl_seconds = robots_cache_ttl_seconds
        self._robots_cache: dict[
            tuple[str, frozenset[str], str, tuple[str, ...]],
            tuple[float, RobotFileParser | None],
        ] = {}
        self._robots_cache_lock = asyncio.Lock()
        self._request_pacing_lock = asyncio.Lock()
        self._last_request_at: dict[str, float] = {}
        if not 1 <= domain_concurrency <= 16:
            raise ValueError("domain concurrency must be between 1 and 16")
        self._domain_concurrency = domain_concurrency
        self._request_semaphores: dict[str, asyncio.Semaphore] = {}

    async def crawl(self, policy: WebCrawlPolicy) -> WebCrawlResult:
        _validate_policy(policy)
        base_url = canonicalize_url(policy.base_url)
        base_host = host_for_url(base_url)
        allowed_hosts = frozenset(
            {base_host, *(host.casefold() for host in policy.allowed_hosts if host.strip())}
        )
        sitemap_entries, sitemap_errors, sitemap_truncated = await self._sitemap_discovery.discover(
            policy,
            allowed_hosts=allowed_hosts,
        )
        validators = _validator_map(
            policy,
            base_url=base_url,
            allowed_hosts=allowed_hosts,
            enabled=bool(sitemap_entries) or not policy.follow_internal_links,
        )
        robots = await self._load_robots(policy, base_url, allowed_hosts)
        seed_entries = tuple(SitemapEntry(url) for url in policy.seed_urls)
        initial_entries = (*seed_entries, *sitemap_entries) or (SitemapEntry(base_url),)
        queue: deque[SitemapEntry] = deque(initial_entries)
        discovered: set[str] = {entry.url for entry in queue}
        visited: set[str] = set()
        documents = []
        unchanged_documents: list[UnchangedWebDocument] = []
        excluded_count = 0
        failed_count = 0
        errors = dict(sitemap_errors)
        deduplicator = WebDocumentDeduplicator()
        while queue and len(visited) < policy.max_pages:
            entry = queue.popleft()
            try:
                url = canonicalize_url(entry.url, base_url=base_url, allowed_hosts=allowed_hosts)
            except ValueError as error:
                excluded_count += 1
                errors[entry.url] = f"excluded: {error}"
                continue
            if url in visited:
                continue
            visited.add(url)
            if is_excluded_web_url(url) or is_translated_url(url, policy.translated_locales):
                excluded_count += 1
                if is_translated_url(url, policy.translated_locales):
                    errors[url] = "excluded: translated_language_path"
                continue
            if robots is not None and not robots.can_fetch(policy.user_agent, url):
                excluded_count += 1
                errors[url] = "excluded: robots_txt_disallow"
                continue
            try:
                semaphore = self._request_semaphores.setdefault(
                    base_host, asyncio.Semaphore(self._domain_concurrency)
                )
                async with semaphore:
                    await self._wait_for_request_slot(base_host, policy.crawl_delay_seconds)
                    validator = validators.get(url)
                    response = await self._fetcher.fetch(
                        WebFetchRequest(
                            url=url,
                            allowed_hosts=allowed_hosts,
                            timeout_seconds=policy.request_timeout_seconds,
                            max_response_bytes=policy.max_response_bytes,
                            max_decompressed_bytes=policy.max_decompressed_response_bytes,
                            max_compression_ratio=policy.max_compression_ratio,
                            user_agent=policy.user_agent,
                            accepted_content_types=frozenset(
                                {"text/html", "application/xhtml+xml"}
                            ),
                            if_none_match=None if validator is None else validator.etag,
                            if_modified_since=(
                                None if validator is None else validator.last_modified
                            ),
                            blocked_first_path_segments=frozenset(policy.translated_locales),
                        )
                    )
                if response.status_code == 304:
                    if validator is None:
                        raise ValueError("page returned HTTP 304 without a known validator")
                    unchanged_documents.append(
                        UnchangedWebDocument(
                            document_id=validator.document_id,
                            version_id=validator.version_id,
                            canonical_url=validator.canonical_url,
                            checked_at=datetime.now(UTC),
                            etag=response.headers.get("etag") or validator.etag,
                            last_modified=(
                                response.headers.get("last-modified") or validator.last_modified
                            ),
                            product_key=validator.product_key,
                        )
                    )
                    continue
                if response.status_code in {404, 410}:
                    excluded_count += 1
                    errors[url] = f"excluded: gone:http_{response.status_code}"
                    continue
                if response.status_code in {429, 503}:
                    raise RetryableWebFetchError(
                        response.status_code,
                        _retry_after_seconds(response.headers.get("retry-after")),
                    )
                if not 200 <= response.status_code < 300:
                    raise ValueError(f"page returned HTTP {response.status_code}")
                parsed = self._html_parser.parse(
                    requested_url=url,
                    final_url=response.final_url,
                    body=response.body,
                    content_type=response.content_type,
                    allowed_hosts=allowed_hosts,
                )
                robots_directives = parsed.meta.get("robots", "").casefold()
                if "noindex" in {part.strip() for part in robots_directives.split(",")}:
                    excluded_count += 1
                    errors[url] = "excluded: noindex"
                    continue
                if policy.content_kind != "general" and not (
                    policy.content_kind == "guide" and parsed.content_kind == "category"
                ):
                    parsed = replace(parsed, content_kind=policy.content_kind)
                if is_translated_url(parsed.final_url, policy.translated_locales):
                    excluded_count += 1
                    errors[url] = "excluded: translated_redirect_target"
                    continue
                if is_translated_url(parsed.canonical_url, policy.translated_locales):
                    excluded_count += 1
                    errors[url] = "excluded: translated_canonical_url"
                    continue
                if policy.enforce_primary_language and not language_matches_primary(
                    parsed.language,
                    policy.language,
                ):
                    excluded_count += 1
                    errors[url] = "excluded: page_language_mismatch"
                    continue
                product = self._product_extractor.extract(parsed)
                document = self._content_extractor.extract(
                    page=parsed,
                    tenant_id=policy.tenant_id,
                    site_id=policy.site_id,
                    fetched_at=datetime.now(UTC),
                    product=product,
                    last_modified=entry.last_modified or response.headers.get("last-modified"),
                    etag=response.headers.get("etag"),
                    language_fallback=policy.language,
                    response_bytes=len(response.body),
                )
                decision = deduplicator.consider(document)
                if decision.accepted:
                    documents.append(document)
                else:
                    excluded_count += 1
                    errors[url] = f"excluded: {decision.reason}"
                if policy.follow_internal_links:
                    for link in parsed.internal_links:
                        if (
                            link not in discovered
                            and not is_excluded_web_url(link)
                            and not is_translated_url(link, policy.translated_locales)
                        ):
                            discovered.add(link)
                            queue.append(SitemapEntry(link))
            except UnsupportedWebContentTypeError as error:
                excluded_count += 1
                errors[url] = f"excluded: unsupported_content_type:{error.content_type}"
            except ResponseBudgetExceededError as error:
                failed_count += 1
                errors[url] = f"response_budget_exceeded:{error.reason_code}"
            except UnusableWebContentError as error:
                excluded_count += 1
                reason = (
                    "approved_utility_page_without_knowledge_content"
                    if policy.content_kind == "utility"
                    else f"unusable_content:{error.reason_code}"
                )
                errors[url] = f"excluded: {reason}"
            except RetryableWebFetchError:
                raise
            except Exception as error:  # noqa: BLE001
                failed_count += 1
                errors[url] = f"{type(error).__name__}: {error}"

        return WebCrawlResult(
            discovered_count=len(discovered),
            documents=tuple(documents),
            excluded_count=excluded_count,
            failed_count=failed_count,
            errors=errors,
            unchanged_documents=tuple(unchanged_documents),
            truncated=sitemap_truncated or bool(queue),
        )

    async def _wait_for_request_slot(self, host: str, delay_seconds: float) -> None:
        if delay_seconds <= 0:
            return
        async with self._request_pacing_lock:
            loop = asyncio.get_running_loop()
            last_request_at = self._last_request_at.get(host)
            if last_request_at is not None:
                elapsed = loop.time() - last_request_at
                if elapsed < delay_seconds:
                    await asyncio.sleep(delay_seconds - elapsed)
            self._last_request_at[host] = loop.time()

    async def _load_robots(
        self,
        policy: WebCrawlPolicy,
        base_url: str,
        allowed_hosts: frozenset[str],
    ) -> RobotFileParser | None:
        if not policy.respect_robots_txt:
            return None
        robots_url = urljoin(base_url, "/robots.txt")
        cache_key = (
            robots_url,
            allowed_hosts,
            policy.user_agent,
            tuple(policy.translated_locales),
        )
        async with self._robots_cache_lock:
            now = asyncio.get_running_loop().time()
            cached = self._robots_cache.get(cache_key)
            if cached is not None and cached[0] > now:
                return cached[1]
            try:
                response = await self._fetcher.fetch(
                    WebFetchRequest(
                        url=robots_url,
                        allowed_hosts=allowed_hosts,
                        timeout_seconds=policy.request_timeout_seconds,
                        max_response_bytes=min(policy.max_response_bytes, 512_000),
                        max_decompressed_bytes=min(
                            policy.max_decompressed_response_bytes,
                            512_000,
                        ),
                        max_compression_ratio=policy.max_compression_ratio,
                        user_agent=policy.user_agent,
                        accepted_content_types=frozenset({"text/plain", "text/html"}),
                        blocked_first_path_segments=frozenset(policy.translated_locales),
                    )
                )
                parser = None
                if 200 <= response.status_code < 300:
                    parser = RobotFileParser()
                    parser.set_url(robots_url)
                    parser.parse(response.body.decode("utf-8", errors="replace").splitlines())
                self._robots_cache[cache_key] = (
                    now + self._robots_cache_ttl_seconds,
                    parser,
                )
                return parser
            except Exception:  # noqa: BLE001
                return None


def _validate_policy(policy: WebCrawlPolicy) -> None:
    if not policy.tenant_id.strip() or not policy.site_id.strip():
        raise ValueError("tenant_id and site_id are required")
    if not 1 <= policy.max_pages <= 1_000_000:
        raise ValueError("max_pages must be between 1 and 1000000")
    if not 1 <= policy.max_sitemaps <= 100:
        raise ValueError("max_sitemaps must be between 1 and 100")
    if not 1_000 <= policy.max_response_bytes <= 20_000_000:
        raise ValueError("max_response_bytes is outside the safe range")
    if not policy.max_response_bytes <= policy.max_decompressed_response_bytes <= 20_000_000:
        raise ValueError("max_decompressed_response_bytes is outside the safe range")
    if not 1 <= policy.max_compression_ratio <= 100:
        raise ValueError("max_compression_ratio must be between 1 and 100")
    if not 0 <= policy.crawl_delay_seconds <= 60:
        raise ValueError("crawl_delay_seconds must be between 0 and 60")
    if not 1 <= policy.authority_level <= 100 or not 1 <= policy.priority <= 100:
        raise ValueError("authority_level and priority must be between 1 and 100")
    if not 1 <= policy.batch_size <= 1000:
        raise ValueError("batch_size must be between 1 and 1000")


def _retry_after_seconds(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    cleaned = value.strip()
    if cleaned.isdigit():
        return min(3600, max(0, int(cleaned)))
    try:
        parsed = parsedate_to_datetime(cleaned)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return min(3600, max(0, int((parsed - datetime.now(UTC)).total_seconds())))


def _validator_map(
    policy: WebCrawlPolicy,
    *,
    base_url: str,
    allowed_hosts: frozenset[str],
    enabled: bool,
) -> dict[str, WebCrawlValidator]:
    if not enabled:
        return {}
    result = {}
    for validator in policy.validators:
        if validator.etag is None and validator.last_modified is None:
            continue
        for candidate in (
            validator.canonical_url,
            validator.requested_url,
            validator.final_url,
        ):
            try:
                url = canonicalize_url(
                    candidate,
                    base_url=base_url,
                    allowed_hosts=allowed_hosts,
                )
            except ValueError:
                continue
            result[url] = validator
    return result
