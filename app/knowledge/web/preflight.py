import asyncio
from collections import deque
from pathlib import PurePosixPath
from urllib.parse import unquote, urljoin, urlsplit

from app.domain.models.web_crawl_manifest import (
    WebCrawlDiscoveryAttempt,
    WebCrawlManifestItem,
)
from app.domain.ports.web_crawl_preflight import (
    WebCrawlInspection,
    WebCrawlInspectionRequest,
)
from app.domain.ports.web_knowledge import WebFetchRequest, WebPageFetcherPort
from app.knowledge.web.canonicalizer import canonicalize_url, host_for_url, origin_for_url
from app.knowledge.web.language_scope import (
    classify_sitemap_locales,
    is_translated_url,
    language_matches_primary,
    normalize_language_tag,
)
from app.knowledge.web.models import SitemapDocument
from app.knowledge.web.sitemap import SitemapParser
from app.knowledge.web.sitemap_locator import (
    SITEMAP_CONTENT_TYPES,
    SitemapEntrypointLocator,
    sitemap_failure_outcome,
)
from app.knowledge.web.url_scope import is_excluded_web_url


class GTranslateWebCrawlPreflightInspector:
    def __init__(
        self,
        *,
        fetcher: WebPageFetcherPort,
        parser: SitemapParser | None = None,
        locator: SitemapEntrypointLocator | None = None,
    ) -> None:
        self._fetcher = fetcher
        self._parser = parser or SitemapParser()
        self._locator = locator or SitemapEntrypointLocator(
            fetcher=fetcher,
            parser=self._parser,
        )

    async def inspect(self, request: WebCrawlInspectionRequest) -> WebCrawlInspection:
        if request.translation_provider != "gtranslate":
            raise ValueError("unsupported translation provider")
        if request.max_urls < 1:
            raise ValueError("max_urls must be positive")
        primary_language = normalize_language_tag(request.primary_language)
        base_url = canonicalize_url(request.base_url)
        page_allowed_hosts = frozenset({host_for_url(base_url)})
        page_allowed_origins = frozenset({origin_for_url(base_url)})
        sitemap_allowed_origins = _sitemap_origin_boundary(
            base_url,
            request.allowed_sitemap_origins,
        )
        sitemap_allowed_hosts = frozenset(
            host_for_url(origin) for origin in sitemap_allowed_origins
        )
        location = await self._locator.locate(
            request,
            base_url=base_url,
            allowed_hosts=sitemap_allowed_hosts,
            allowed_origins=sitemap_allowed_origins,
        )
        root_sitemap_urls = tuple(root.url for root in location.roots)
        root_sitemap_url = (
            root_sitemap_urls[0]
            if root_sitemap_urls
            else canonicalize_url(
                urljoin(base_url, "/sitemap.xml"),
                allowed_hosts=page_allowed_hosts,
                keep_xml=True,
                preserve_query=True,
            )
        )
        queue: deque[tuple[str, SitemapDocument | None]] = deque()
        queued: set[str] = set()
        visited: set[str] = set()
        primary_sitemaps: set[str] = set()
        translated_locales: set[str] = set()
        excluded_sitemaps: set[str] = set()
        raw_items: dict[str, WebCrawlManifestItem] = {}
        blocking_reasons: list[str] = list(location.blocking_reasons)
        discovery_attempts = list(location.attempts)
        warnings = list(location.warnings)
        url_limit_reached = False

        root_classifications, root_locales = classify_sitemap_locales(
            root_sitemap_urls,
            primary_language=primary_language,
        )
        translated_locales.update(root_locales)
        for root in location.roots:
            locale = root_classifications[root.url]
            if locale is not None and not language_matches_primary(locale, primary_language):
                excluded_sitemaps.add(root.url)
                continue
            if root.url not in queued:
                queue.append((root.url, root.document))
                queued.add(root.url)

        while queue and len(visited) < request.max_sitemaps:
            sitemap_url, prefetched_document = queue.popleft()
            queued.discard(sitemap_url)
            if sitemap_url in visited:
                continue
            visited.add(sitemap_url)
            if prefetched_document is None:
                document = None
                final_url = sitemap_url
                for attempt_number in range(2):
                    try:
                        response = await self._fetcher.fetch(
                            WebFetchRequest(
                                url=sitemap_url,
                                allowed_hosts=sitemap_allowed_hosts,
                                allowed_origins=sitemap_allowed_origins,
                                preserve_query=True,
                                timeout_seconds=request.request_timeout_seconds,
                                max_response_bytes=request.max_response_bytes,
                                max_decompressed_bytes=(request.max_decompressed_response_bytes),
                                max_compression_ratio=request.max_compression_ratio,
                                user_agent=request.user_agent,
                                accepted_content_types=SITEMAP_CONTENT_TYPES,
                            )
                        )
                        if not 200 <= response.status_code < 300:
                            raise ValueError(f"sitemap returned HTTP {response.status_code}")
                        parsed_document = self._parser.parse(
                            response.body,
                            max_decompressed_bytes=(request.max_decompressed_response_bytes),
                            max_compression_ratio=request.max_compression_ratio,
                        )
                        final_url = canonicalize_url(
                            response.final_url,
                            allowed_hosts=sitemap_allowed_hosts,
                            keep_xml=True,
                            preserve_query=True,
                        )
                        if origin_for_url(final_url) not in sitemap_allowed_origins:
                            raise ValueError("redirect is outside the verified origin")
                        document = parsed_document
                    except Exception as error:  # noqa: BLE001
                        outcome = sitemap_failure_outcome(error)
                        if attempt_number == 0:
                            discovery_attempts.append(
                                WebCrawlDiscoveryAttempt(
                                    sitemap_url,
                                    "nested",
                                    f"retrying_{outcome}",
                                )
                            )
                            await asyncio.sleep(0.25)
                            continue
                        blocking_reasons.append("sitemap_tree_incomplete")
                        discovery_attempts.append(
                            WebCrawlDiscoveryAttempt(
                                sitemap_url,
                                "nested",
                                outcome,
                            )
                        )
                    else:
                        discovery_attempts.append(
                            WebCrawlDiscoveryAttempt(
                                sitemap_url,
                                "nested",
                                "accepted",
                                final_url,
                            )
                        )
                        if attempt_number:
                            warnings.append("sitemap_retry_recovered")
                        break
                if document is None:
                    continue
            else:
                document = prefetched_document
                final_url = sitemap_url

            if document.pages:
                primary_sitemaps.add(final_url)
                for entry in document.pages:
                    try:
                        url = canonicalize_url(
                            entry.url,
                            base_url=final_url,
                            allowed_hosts=page_allowed_hosts,
                        )
                        if origin_for_url(url) not in page_allowed_origins:
                            raise ValueError("page URL is outside the verified origin")
                    except ValueError:
                        continue
                    if url in raw_items:
                        continue
                    if len(raw_items) >= request.max_urls:
                        url_limit_reached = True
                        break
                    raw_items[url] = WebCrawlManifestItem(
                        url=url,
                        source_sitemap_url=final_url,
                        content_kind=_content_kind(url, final_url),
                        last_modified=entry.last_modified,
                    )

            if url_limit_reached:
                blocking_reasons.append("manifest_url_limit_reached")
                queue.clear()
                break

            nested_urls: list[str] = []
            for entry in document.nested_sitemaps:
                try:
                    nested_url = canonicalize_url(
                        entry.url,
                        base_url=final_url,
                        allowed_hosts=sitemap_allowed_hosts,
                        keep_xml=True,
                        preserve_query=True,
                    )
                    if origin_for_url(nested_url) not in sitemap_allowed_origins:
                        raise ValueError("nested sitemap is outside the verified origin")
                    nested_urls.append(nested_url)
                except ValueError:
                    blocking_reasons.append("untrusted_or_invalid_sitemap_url")
            classifications, locales = classify_sitemap_locales(
                tuple(nested_urls),
                primary_language=primary_language,
            )
            translated_locales.update(locales)
            for nested_url in nested_urls:
                locale = classifications[nested_url]
                if locale is not None and not language_matches_primary(
                    locale,
                    primary_language,
                ):
                    excluded_sitemaps.add(nested_url)
                    continue
                if nested_url not in visited and nested_url not in queued:
                    queue.append((nested_url, None))
                    queued.add(nested_url)

        if queue:
            blocking_reasons.append("sitemap_limit_reached")

        translated_tuple = tuple(sorted(translated_locales))
        items = tuple(
            item
            for item in sorted(raw_items.values(), key=lambda value: value.url)
            if not is_translated_url(item.url, translated_tuple)
            and not is_excluded_web_url(item.url)
        )
        excluded_url_count = len(raw_items) - len(items)
        if any(is_translated_url(item.url, translated_tuple) for item in items):
            blocking_reasons.append("translated_url_remained_in_manifest")
        if not items:
            blocking_reasons.append("no_primary_language_urls")

        return WebCrawlInspection(
            root_sitemap_url=root_sitemap_url,
            primary_sitemap_urls=tuple(sorted(primary_sitemaps)),
            translated_locales=translated_tuple,
            excluded_sitemap_count=len(excluded_sitemaps),
            excluded_url_count=excluded_url_count,
            blocking_reasons=tuple(dict.fromkeys(blocking_reasons)),
            items=items,
            root_sitemap_urls=root_sitemap_urls,
            discovery_method=location.method,
            warnings=tuple(dict.fromkeys(warnings)),
            coverage_status=(
                "declared_complete" if items and not blocking_reasons else "incomplete"
            ),
            discovery_attempts=tuple(discovery_attempts),
        )


def _content_kind(url: str, sitemap_url: str) -> str:
    path = unquote(urlsplit(url).path).casefold()
    sitemap_name = PurePosixPath(urlsplit(sitemap_url).path).name.casefold()
    if path.startswith("/guides/"):
        return "guide"
    if any(token in PurePosixPath(path).stem for token in ("converter", "calculator")):
        return "utility"
    if "product" in sitemap_name:
        return "product"
    if any(
        token in path
        for token in (
            "policy",
            "shipping",
            "delivery",
            "return",
            "refund",
            "privacy",
            "payment",
            "warranty",
            "terms",
            "versand",
            "liefer",
            "rückgabe",
            "rueckgabe",
            "widerruf",
            "datenschutz",
            "zahlung",
            "garantie",
            "gewaehrleistung",
        )
    ):
        return "policy"
    return "general"


def _sitemap_origin_boundary(
    base_url: str,
    configured_origins: tuple[str, ...],
) -> frozenset[str]:
    origins = {origin_for_url(base_url)}
    for value in configured_origins:
        parsed = urlsplit(value.strip())
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("allowed sitemap origin contains an invalid port") from exc
        if (
            parsed.scheme.casefold() != "https"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or port not in {None, 443}
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("allowed sitemap origins must be exact HTTPS origins")
        origins.add(origin_for_url(value))
    return frozenset(origins)
