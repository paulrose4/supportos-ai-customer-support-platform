import gzip
from collections import deque
from io import BytesIO
from urllib.parse import urljoin
from xml.parsers import expat

from app.domain.ports.web_knowledge import (
    ResponseBudgetExceededError,
    WebFetchRequest,
    WebPageFetcherPort,
)
from app.knowledge.web.canonicalizer import canonicalize_url, host_for_url
from app.knowledge.web.models import SitemapDocument, SitemapEntry, WebCrawlPolicy


class SitemapParser:
    def __init__(self, *, max_entries: int = 100_000) -> None:
        self._max_entries = max_entries

    def parse(
        self,
        body: bytes,
        *,
        max_decompressed_bytes: int = 20_000_000,
        max_compression_ratio: float = 50.0,
    ) -> SitemapDocument:
        if body.startswith(b"\x1f\x8b"):
            body = _bounded_gzip_decompress(
                body,
                max_bytes=max_decompressed_bytes,
                max_ratio=max_compression_ratio,
            )
        handler = _SitemapXmlHandler(max_entries=self._max_entries)
        parser = expat.ParserCreate(namespace_separator="}")
        parser.buffer_text = True
        parser.StartElementHandler = handler.start_element
        parser.EndElementHandler = handler.end_element
        parser.CharacterDataHandler = handler.character_data
        parser.StartDoctypeDeclHandler = _reject_xml_declaration
        parser.EntityDeclHandler = _reject_xml_declaration
        parser.UnparsedEntityDeclHandler = _reject_xml_declaration
        parser.ExternalEntityRefHandler = _reject_xml_declaration
        parser.SkippedEntityHandler = _reject_xml_declaration
        parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
        try:
            parser.Parse(body, True)
        except expat.ExpatError as error:
            raise ValueError("invalid sitemap XML") from error
        return handler.document()


class SitemapDiscovery:
    def __init__(self, fetcher: WebPageFetcherPort, parser: SitemapParser | None = None) -> None:
        self._fetcher = fetcher
        self._parser = parser or SitemapParser()

    async def discover(
        self,
        policy: WebCrawlPolicy,
        *,
        allowed_hosts: frozenset[str],
    ) -> tuple[tuple[SitemapEntry, ...], dict[str, str], bool]:
        if not policy.discover_sitemaps:
            return (), {}, False
        initial = policy.sitemap_urls or (
            urljoin(policy.base_url.rstrip("/") + "/", "sitemap.xml"),
        )
        queue: deque[str] = deque()
        for value in initial:
            try:
                queue.append(
                    canonicalize_url(
                        value,
                        base_url=policy.base_url,
                        allowed_hosts=allowed_hosts,
                        keep_xml=True,
                        preserve_query=True,
                    )
                )
            except ValueError:
                continue
        visited: set[str] = set()
        pages: dict[str, SitemapEntry] = {}
        errors: dict[str, str] = {}
        while queue and len(visited) < policy.max_sitemaps:
            sitemap_url = queue.popleft()
            if sitemap_url in visited:
                continue
            visited.add(sitemap_url)
            try:
                response = await self._fetcher.fetch(
                    WebFetchRequest(
                        url=sitemap_url,
                        allowed_hosts=allowed_hosts,
                        preserve_query=True,
                        timeout_seconds=policy.request_timeout_seconds,
                        max_response_bytes=policy.max_response_bytes,
                        user_agent=policy.user_agent,
                    )
                )
                if not 200 <= response.status_code < 300:
                    raise ValueError(f"sitemap returned HTTP {response.status_code}")
                parsed = self._parser.parse(response.body)
                for entry in parsed.pages:
                    try:
                        canonical = canonicalize_url(
                            entry.url,
                            base_url=response.final_url,
                            allowed_hosts=allowed_hosts,
                        )
                    except ValueError:
                        continue
                    pages.setdefault(canonical, SitemapEntry(canonical, entry.last_modified))
                for entry in parsed.nested_sitemaps:
                    try:
                        nested = canonicalize_url(
                            entry.url,
                            base_url=response.final_url,
                            allowed_hosts=allowed_hosts,
                            keep_xml=True,
                            preserve_query=True,
                        )
                    except ValueError:
                        continue
                    if host_for_url(nested) in allowed_hosts and nested not in visited:
                        queue.append(nested)
            except Exception as error:  # noqa: BLE001
                errors[sitemap_url] = f"{type(error).__name__}: {error}"
        truncated = any(sitemap_url not in visited for sitemap_url in queue)
        return tuple(pages.values()), errors, truncated


def _local_name(value: str) -> str:
    return value.rsplit("}", 1)[-1].casefold()


class _SitemapXmlHandler:
    def __init__(self, *, max_entries: int) -> None:
        self._max_entries = max_entries
        self._root_name: str | None = None
        self._stack: list[str] = []
        self._entries: list[SitemapEntry] = []
        self._location: str | None = None
        self._last_modified: str | None = None
        self._capture_name: str | None = None
        self._capture_chunks: list[str] = []

    def start_element(self, name: str, attributes: dict[str, str]) -> None:
        del attributes
        local_name = _local_name(name)
        depth = len(self._stack)
        if depth == 0:
            if local_name not in {"urlset", "sitemapindex"}:
                raise ValueError("unsupported sitemap root element")
            self._root_name = local_name
        elif depth == 1:
            expected_child = "url" if self._root_name == "urlset" else "sitemap"
            if local_name != expected_child:
                raise ValueError("sitemap contains an invalid root child element")
            self._location = None
            self._last_modified = None
        elif depth == 2 and local_name in {"loc", "lastmod"}:
            self._capture_name = local_name
            self._capture_chunks = []
        elif self._capture_name is not None:
            raise ValueError("sitemap loc and lastmod values must contain text only")
        self._stack.append(local_name)

    def end_element(self, name: str) -> None:
        local_name = _local_name(name)
        if not self._stack or self._stack[-1] != local_name:
            raise ValueError("invalid sitemap XML element nesting")
        depth = len(self._stack)
        if self._capture_name == local_name and depth == 3:
            value = "".join(self._capture_chunks).strip()
            if local_name == "loc":
                self._location = value or None
            else:
                self._last_modified = value[:100] or None
            self._capture_name = None
            self._capture_chunks = []
        elif depth == 2:
            if self._location:
                self._entries.append(SitemapEntry(self._location, self._last_modified))
                if len(self._entries) > self._max_entries:
                    raise ValueError("sitemap exceeds configured entry limit")
            self._location = None
            self._last_modified = None
        self._stack.pop()

    def character_data(self, value: str) -> None:
        if self._capture_name is not None:
            self._capture_chunks.append(value)

    def document(self) -> SitemapDocument:
        entries = tuple(self._entries)
        if self._root_name == "urlset":
            return SitemapDocument(pages=entries)
        if self._root_name == "sitemapindex":
            return SitemapDocument(nested_sitemaps=entries)
        raise ValueError("invalid sitemap XML")


def _reject_xml_declaration(*_args: object) -> int:
    raise ValueError("DTD and entity declarations are prohibited in sitemaps")


def _bounded_gzip_decompress(value: bytes, *, max_bytes: int, max_ratio: float) -> bytes:
    ratio_limit = max(1, int(len(value) * max_ratio))
    read_limit = min(max_bytes, ratio_limit)
    with gzip.GzipFile(fileobj=BytesIO(value)) as archive:
        result = archive.read(read_limit + 1)
    if len(result) > max_bytes:
        raise ResponseBudgetExceededError("decompressed_bytes_exceeded")
    if len(result) > ratio_limit:
        raise ResponseBudgetExceededError("compression_ratio_exceeded")
    return result
