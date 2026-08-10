import asyncio
import re
from dataclasses import dataclass
from urllib.parse import urljoin

from app.domain.models.web_crawl_manifest import WebCrawlDiscoveryAttempt
from app.domain.ports.web_crawl_preflight import WebCrawlInspectionRequest
from app.domain.ports.web_knowledge import (
    ResponseBudgetExceededError,
    UnsupportedWebContentTypeError,
    WebFetchRequest,
    WebPageFetcherPort,
    WebTransportError,
)
from app.knowledge.web.canonicalizer import canonicalize_url, origin_for_url
from app.knowledge.web.models import SitemapDocument
from app.knowledge.web.sitemap import SitemapParser

SITEMAP_CONTENT_TYPES = frozenset({"*"})
_COMMON_SITEMAP_PATHS = (
    "/sitemap.xml",
    "/sitemap_index.xml",
    "/sitemap-index.xml",
    "/wp-sitemap.xml",
    "/sitemap.xml.gz",
)
_SITEMAP_DIRECTIVE_PATTERN = re.compile(r"^\s*sitemap\s*:\s*(\S.*?)\s*$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class LocatedSitemap:
    url: str
    document: SitemapDocument


@dataclass(frozen=True, slots=True)
class SitemapLocationResult:
    roots: tuple[LocatedSitemap, ...]
    method: str
    attempts: tuple[WebCrawlDiscoveryAttempt, ...]
    warnings: tuple[str, ...] = ()
    blocking_reasons: tuple[str, ...] = ()


class SitemapEntrypointLocator:
    def __init__(
        self,
        *,
        fetcher: WebPageFetcherPort,
        parser: SitemapParser | None = None,
    ) -> None:
        self._fetcher = fetcher
        self._parser = parser or SitemapParser()

    async def locate(
        self,
        request: WebCrawlInspectionRequest,
        *,
        base_url: str,
        allowed_hosts: frozenset[str],
        allowed_origins: frozenset[str],
    ) -> SitemapLocationResult:
        mode = request.discovery_mode.strip().casefold()
        if mode not in {"auto", "hybrid", "manual"}:
            raise ValueError("unsupported sitemap discovery mode")

        attempts: list[WebCrawlDiscoveryAttempt] = []
        warnings: list[str] = []
        explicit_urls = _canonical_candidates(
            request.explicit_sitemap_urls,
            base_url=base_url,
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins,
            source="manual",
            attempts=attempts,
        )
        if explicit_urls and mode in {"hybrid", "manual"}:
            roots, invalid_count = await self._validate_all(
                explicit_urls,
                source="manual",
                request=request,
                allowed_hosts=allowed_hosts,
                allowed_origins=allowed_origins,
                attempts=attempts,
            )
            if roots and invalid_count == 0:
                return SitemapLocationResult(
                    roots=roots,
                    method="manual",
                    attempts=tuple(attempts),
                )
            if mode == "manual":
                reasons = (
                    ("configured_sitemap_incomplete",) if roots else ("sitemap_not_discovered",)
                )
                return SitemapLocationResult(
                    roots=roots,
                    method="manual",
                    attempts=tuple(attempts),
                    blocking_reasons=reasons,
                )
            warnings.append("configured_sitemap_fallback_used")
        elif mode == "manual":
            return SitemapLocationResult(
                roots=(),
                method="manual",
                attempts=tuple(attempts),
                blocking_reasons=("configured_sitemap_required",),
            )
        elif request.explicit_sitemap_urls and mode == "hybrid":
            warnings.append("configured_sitemap_fallback_used")

        robots_candidates = await self._robots_candidates(
            request,
            base_url=base_url,
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins,
            attempts=attempts,
        )
        if robots_candidates:
            roots, invalid_count = await self._validate_all(
                robots_candidates,
                source="robots_txt",
                request=request,
                allowed_hosts=allowed_hosts,
                allowed_origins=allowed_origins,
                attempts=attempts,
            )
            if roots:
                return SitemapLocationResult(
                    roots=roots,
                    method="robots_txt",
                    attempts=tuple(attempts),
                    warnings=tuple(warnings),
                    blocking_reasons=("sitemap_roots_incomplete",) if invalid_count else (),
                )

        previous_urls = _canonical_candidates(
            request.previous_sitemap_urls,
            base_url=base_url,
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins,
            source="last_known_good",
            attempts=attempts,
        )
        if previous_urls:
            roots, invalid_count = await self._validate_all(
                previous_urls,
                source="last_known_good",
                request=request,
                allowed_hosts=allowed_hosts,
                allowed_origins=allowed_origins,
                attempts=attempts,
            )
            if roots and invalid_count == 0:
                return SitemapLocationResult(
                    roots=roots,
                    method="last_known_good",
                    attempts=tuple(attempts),
                    warnings=tuple((*warnings, "last_known_good_sitemap_used")),
                )

        common_candidates = tuple(
            canonicalize_url(
                urljoin(base_url, path),
                allowed_hosts=allowed_hosts,
                keep_xml=True,
                preserve_query=True,
            )
            for path in _COMMON_SITEMAP_PATHS
        )
        common_roots, _invalid_count = await self._validate_concurrently(
            common_candidates,
            source="common_path",
            request=request,
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins,
            attempts=attempts,
        )
        if common_roots:
            return SitemapLocationResult(
                roots=common_roots,
                method="common_path",
                attempts=tuple(attempts),
                warnings=tuple(warnings),
            )

        return SitemapLocationResult(
            roots=(),
            method="none",
            attempts=tuple(attempts),
            warnings=tuple(warnings),
            blocking_reasons=("sitemap_not_discovered",),
        )

    async def _robots_candidates(
        self,
        request: WebCrawlInspectionRequest,
        *,
        base_url: str,
        allowed_hosts: frozenset[str],
        allowed_origins: frozenset[str],
        attempts: list[WebCrawlDiscoveryAttempt],
    ) -> tuple[str, ...]:
        robots_url = canonicalize_url(
            urljoin(base_url, "/robots.txt"),
            allowed_hosts=allowed_hosts,
        )
        try:
            response = await self._fetcher.fetch(
                WebFetchRequest(
                    url=robots_url,
                    allowed_hosts=allowed_hosts,
                    allowed_origins=frozenset({origin_for_url(base_url)}),
                    preserve_query=True,
                    timeout_seconds=request.request_timeout_seconds,
                    max_response_bytes=min(request.max_response_bytes, 512_000),
                    max_decompressed_bytes=min(request.max_response_bytes, 512_000),
                    user_agent=request.user_agent,
                    accepted_content_types=frozenset({"text/plain", "text/html"}),
                )
            )
            if not 200 <= response.status_code < 300:
                attempts.append(
                    WebCrawlDiscoveryAttempt(robots_url, "robots_txt", "http_not_found")
                )
                return ()
            values = _parse_sitemap_directives(response.body)
            candidates = _canonical_candidates(
                values,
                base_url=response.final_url,
                allowed_hosts=allowed_hosts,
                allowed_origins=allowed_origins,
                source="robots_txt",
                attempts=attempts,
            )
            attempts.append(
                WebCrawlDiscoveryAttempt(
                    robots_url,
                    "robots_txt",
                    "sitemaps_found" if candidates else "no_sitemaps",
                    response.final_url,
                )
            )
            return candidates
        except Exception:  # noqa: BLE001
            attempts.append(WebCrawlDiscoveryAttempt(robots_url, "robots_txt", "fetch_failed"))
            return ()

    async def _validate_all(
        self,
        urls: tuple[str, ...],
        *,
        source: str,
        request: WebCrawlInspectionRequest,
        allowed_hosts: frozenset[str],
        allowed_origins: frozenset[str],
        attempts: list[WebCrawlDiscoveryAttempt],
    ) -> tuple[tuple[LocatedSitemap, ...], int]:
        roots: list[LocatedSitemap] = []
        invalid_count = 0
        final_urls: set[str] = set()
        for url in urls:
            located = await self._validate_candidate(
                url,
                source=source,
                request=request,
                allowed_hosts=allowed_hosts,
                allowed_origins=allowed_origins,
                attempts=attempts,
            )
            if located is None:
                invalid_count += 1
                continue
            if located.url not in final_urls:
                roots.append(located)
                final_urls.add(located.url)
        return tuple(roots), invalid_count

    async def _validate_concurrently(
        self,
        urls: tuple[str, ...],
        *,
        source: str,
        request: WebCrawlInspectionRequest,
        allowed_hosts: frozenset[str],
        allowed_origins: frozenset[str],
        attempts: list[WebCrawlDiscoveryAttempt],
    ) -> tuple[tuple[LocatedSitemap, ...], int]:
        async def validate(
            url: str,
        ) -> tuple[LocatedSitemap | None, tuple[WebCrawlDiscoveryAttempt, ...]]:
            candidate_attempts: list[WebCrawlDiscoveryAttempt] = []
            located = await self._validate_candidate(
                url,
                source=source,
                request=request,
                allowed_hosts=allowed_hosts,
                allowed_origins=allowed_origins,
                attempts=candidate_attempts,
            )
            return located, tuple(candidate_attempts)

        results = await asyncio.gather(*(validate(url) for url in urls))
        roots: list[LocatedSitemap] = []
        final_urls: set[str] = set()
        invalid_count = 0
        for located, candidate_attempts in results:
            attempts.extend(candidate_attempts)
            if located is None:
                invalid_count += 1
            elif located.url not in final_urls:
                roots.append(located)
                final_urls.add(located.url)
        return tuple(roots), invalid_count

    async def _validate_candidate(
        self,
        url: str,
        *,
        source: str,
        request: WebCrawlInspectionRequest,
        allowed_hosts: frozenset[str],
        allowed_origins: frozenset[str],
        attempts: list[WebCrawlDiscoveryAttempt],
    ) -> LocatedSitemap | None:
        try:
            response = await self._fetcher.fetch(
                WebFetchRequest(
                    url=url,
                    allowed_hosts=allowed_hosts,
                    allowed_origins=allowed_origins,
                    preserve_query=True,
                    timeout_seconds=request.request_timeout_seconds,
                    max_response_bytes=request.max_response_bytes,
                    max_decompressed_bytes=request.max_decompressed_response_bytes,
                    max_compression_ratio=request.max_compression_ratio,
                    user_agent=request.user_agent,
                    accepted_content_types=SITEMAP_CONTENT_TYPES,
                )
            )
            if not 200 <= response.status_code < 300:
                attempts.append(WebCrawlDiscoveryAttempt(url, source, "http_error"))
                return None
            document = self._parser.parse(
                response.body,
                max_decompressed_bytes=request.max_decompressed_response_bytes,
                max_compression_ratio=request.max_compression_ratio,
            )
            final_url = canonicalize_url(
                response.final_url,
                allowed_hosts=allowed_hosts,
                keep_xml=True,
                preserve_query=True,
            )
            if origin_for_url(final_url) not in allowed_origins:
                attempts.append(WebCrawlDiscoveryAttempt(url, source, "untrusted_redirect"))
                return None
            attempts.append(WebCrawlDiscoveryAttempt(url, source, "accepted", final_url))
            return LocatedSitemap(final_url, document)
        except Exception as error:  # noqa: BLE001
            attempts.append(WebCrawlDiscoveryAttempt(url, source, sitemap_failure_outcome(error)))
            return None


def _canonical_candidates(
    values: tuple[str, ...],
    *,
    base_url: str,
    allowed_hosts: frozenset[str],
    allowed_origins: frozenset[str],
    source: str,
    attempts: list[WebCrawlDiscoveryAttempt],
) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        try:
            url = canonicalize_url(
                value,
                base_url=base_url,
                allowed_hosts=allowed_hosts,
                keep_xml=True,
                preserve_query=True,
            )
            if origin_for_url(url) not in allowed_origins:
                raise ValueError("sitemap URL is outside the verified origin")
        except ValueError:
            attempts.append(WebCrawlDiscoveryAttempt(value, source, "invalid_url"))
            continue
        if url not in seen:
            result.append(url)
            seen.add(url)
    return tuple(result)


def sitemap_failure_outcome(error: Exception) -> str:
    if isinstance(error, ResponseBudgetExceededError):
        return error.reason_code
    if isinstance(error, UnsupportedWebContentTypeError):
        return "unsupported_content_type"
    if isinstance(error, PermissionError):
        return "untrusted_target"
    if isinstance(error, WebTransportError | ConnectionError | TimeoutError):
        return "fetch_failed"
    return "invalid_sitemap"


def _parse_sitemap_directives(body: bytes) -> tuple[str, ...]:
    values: list[str] = []
    for line in body.decode("utf-8-sig", errors="replace").splitlines():
        content = re.split(r"\s+#", line, maxsplit=1)[0]
        match = _SITEMAP_DIRECTIVE_PATTERN.match(content)
        if match is not None:
            values.append(match.group(1))
    return tuple(values)
