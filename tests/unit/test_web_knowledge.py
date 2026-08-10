from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.application.dto import RunWebCrawlPreflightCommand, WebCrawlPreflightRuntimePolicy
from app.application.services import WebCrawlPreflightService
from app.domain.models import (
    AuthenticatedPrincipal,
    WebCrawlManifest,
    WebCrawlManifestItem,
    WebCrawlManifestStatus,
)
from app.domain.ports import (
    ProductIdentityConflictError,
    UnsupportedWebContentTypeError,
    WebCrawlInspectionRequest,
    WebFetchRequest,
    WebFetchResponse,
)
from app.domain.ports.web_crawl_preflight import WebCrawlInspection
from app.knowledge import MarkdownChunker
from app.knowledge.models import ParsedKnowledgeDocument
from app.knowledge.web import (
    GTranslateWebCrawlPreflightInspector,
    HtmlKnowledgeParser,
    ProductExtractor,
    SafeHttpFetcher,
    SitemapParser,
    StructuredContentExtractor,
    UnchangedWebDocument,
    WebCrawlPolicy,
    WebCrawlValidator,
    WebDocumentDeduplicator,
    WebDocumentQualityGate,
    WebKnowledgeSyncService,
    WebsiteKnowledgeCrawler,
    canonicalize_url,
)
from app.knowledge.web.canonicalizer import origin_for_url
from app.knowledge.web.errors import RetryableWebFetchError, StagingCleanupRequiredError
from app.knowledge.web.html_fetcher import _create_public_connection, _validate_public_url
from app.knowledge.web.language_scope import (
    classify_sitemap_locales,
    is_translated_url,
)
from app.knowledge.web.models import StructuredWebDocument, WebCrawlResult
from app.knowledge.web.product_snapshot import product_price_conflict
from tests.fakes.adapters import (
    DeterministicEmbeddingProvider,
    DeterministicSparseEmbeddingProvider,
    InMemoryKnowledgeControlPlane,
    InMemoryKnowledgeIndexer,
    InMemoryProductCatalog,
    InMemoryWebCrawlManifestStore,
)


def test_product_price_conflict_detects_prose_vs_structured_offer() -> None:
    document = StructuredWebDocument(
        tenant_id="tenant-a",
        site_id="site-a",
        document_id="doc-1",
        canonical_url="https://shop.example/products/1",
        title="Product",
        language="en",
        body="# Product\n\nNow $419\n\n## Structured product data\n\n- Offer 1: price=509",
        content_hash="hash",
        category="product",
        product={"sku": "SKU-1", "offers": [{"price": "509", "priceCurrency": "USD"}]},
        internal_links=(),
        fetched_at=datetime.now(UTC),
    )

    assert product_price_conflict(document) == ("419", "509")


def test_product_price_conflict_ignores_non_price_numbers() -> None:
    document = StructuredWebDocument(
        tenant_id="tenant-a",
        site_id="site-a",
        document_id="doc-1",
        canonical_url="https://shop.example/products/1",
        title="Product",
        language="en",
        body="# Product\n\nHeight 150 cm, weight 30 kg\n\n## Structured product data",
        content_hash="hash",
        category="product",
        product={"sku": "SKU-1", "offers": [{"price": "509", "priceCurrency": "USD"}]},
        internal_links=(),
        fetched_at=datetime.now(UTC),
    )

    assert product_price_conflict(document) == ()


def test_product_price_conflict_preserves_trailing_integer_zeroes() -> None:
    document = StructuredWebDocument(
        tenant_id="tenant-a",
        site_id="site-a",
        document_id="doc-1",
        canonical_url="https://shop.example/products/1",
        title="Product",
        language="en",
        body="# Product\n\nNow $500\n\n## Structured product data",
        content_hash="hash",
        category="product",
        product={"sku": "SKU-1", "offers": [{"price": "600.00", "priceCurrency": "USD"}]},
        internal_links=(),
        fetched_at=datetime.now(UTC),
    )

    assert product_price_conflict(document) == ("500", "600")


def test_web_chunker_does_not_infer_heading_from_unapproved_hash_prefix() -> None:
    long_block = "# " + "X" * 681
    chunks = MarkdownChunker().chunk(
        ParsedKnowledgeDocument(
            path=Path("page.md"),
            metadata={"document_id": "doc-1"},
            body=f"{long_block}\n\nThis is a sufficiently long ordinary paragraph for indexing.",
            internal_links=(),
            content_hash="hash",
            heading_blocks=frozenset(),
        )
    )

    assert chunks
    assert all(chunk.heading is None for chunk in chunks)


PRODUCT_HTML = b"""<!doctype html>
<html lang="en-US">
<head>
  <title>Example Product</title>
  <meta name="description" content="A detailed product description for customer support answers.">
  <link rel="canonical" href="https://shop.example.com/products/example.html?utm_source=test">
  <script>ignore this analytics script</script>
  <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "Product",
      "name": "Example Product",
      "sku": "SKU-100",
      "brand": {"@type": "Brand", "name": "Example Brand"},
      "material": "TPE",
      "offers": {"price": "339", "priceCurrency": "USD", "availability": "InStock"},
      "additionalProperty": [{"name": "Height", "value": "125cm"}]
    }
  </script>
</head>
<body>
  <header><nav>Home Products Cart Account</nav></header>
  <div class="cookie-modal">Accept cookies and subscribe to our newsletter.</div>
  <div class="product-shell">A</div>
  <main class="product-detail content">
    <h1>Example Product</h1>
    <p>This product has a detailed customer-facing description with useful support information.</p>
    <h2>Specifications</h2>
    <table><tr><th>Weight</th><td>20kg</td></tr></table>
    <ul><li>Ships in discreet packaging</li><li>Standing feet included</li></ul>
    <a href="/faq.html?utm_campaign=test">Frequently asked questions</a>
    <form>
      <input value="private form value"><button>Buy now</button>
      <section class="shipping-details">
        <h2>Shipping Notes</h2>
        <p>Order processing takes about 3-7 days.</p>
        <p>Shipping takes about 7-20 days.</p>
      </section>
    </form>
  </main>
  <footer>Copyright Example Company Follow us on social media</footer>
</body>
</html>"""

FAQ_HTML = b"""<!doctype html><html><head><title>Frequently Asked Questions</title></head>
<body><main><h1>Frequently Asked Questions</h1>
<p>Orders are packed discreetly and customers receive tracking information after dispatch.</p>
<p>Contact the support team when the published documentation does not answer a question.</p>
</main></body></html>"""


class FakeWebFetcher:
    def __init__(
        self,
        responses: dict[
            str,
            WebFetchResponse | Exception | list[WebFetchResponse | Exception],
        ],
    ) -> None:
        self.responses = responses
        self.requests: list[WebFetchRequest] = []

    async def fetch(self, request: WebFetchRequest) -> WebFetchResponse:
        self.requests.append(request)
        response = self.responses.get(request.url)
        if response is None:
            raise ConnectionError(f"no fake response for {request.url}")
        if isinstance(response, list):
            if not response:
                raise AssertionError(f"no fake responses remain for {request.url}")
            response = response.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def response(url: str, body: bytes, content_type: str) -> WebFetchResponse:
    return WebFetchResponse(
        requested_url=url,
        final_url=url,
        status_code=200,
        content_type=content_type,
        body=body,
    )


class NotModifiedHttpResponse:
    headers: dict[str, str] = {}

    def __enter__(self) -> "NotModifiedHttpResponse":
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def geturl(self) -> str:
        return "https://shop.example.com/products/example.html"

    def getcode(self) -> int:
        return 304

    def read(self, size: int) -> bytes:
        raise AssertionError(f"304 response body must not be read: {size}")


class CapturingHttpOpener:
    def __init__(self) -> None:
        self.request = None

    def open(self, request, timeout: float):  # type: ignore[no-untyped-def]
        del timeout
        self.request = request
        return NotModifiedHttpResponse()


class UnsupportedContentHttpResponse:
    headers = {"Content-Type": "application/pdf"}

    def __enter__(self) -> "UnsupportedContentHttpResponse":
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def geturl(self) -> str:
        return "https://shop.example.com/catalog.pdf"

    def getcode(self) -> int:
        return 200

    def read(self, size: int) -> bytes:
        raise AssertionError(f"unsupported response body must not be read: {size}")


class ArbitrarySitemapContentHttpResponse:
    headers = {"Content-Type": "application/x-sitemap+xml"}
    body = b"<?xml version='1.0'?><urlset/>"

    def __enter__(self) -> "ArbitrarySitemapContentHttpResponse":
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def geturl(self) -> str:
        return "https://shop.example.com/custom-map"

    def getcode(self) -> int:
        return 200

    def read(self, size: int) -> bytes:
        assert size > len(self.body)
        return self.body


class RetryableHttpResponse:
    headers = {"Content-Type": "text/plain", "Retry-After": "60"}

    def __enter__(self) -> "RetryableHttpResponse":
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def geturl(self) -> str:
        return "https://shop.example.com/products/example.html"

    def getcode(self) -> int:
        return 429

    def read(self, size: int) -> bytes:
        raise AssertionError(f"error response body must not be read: {size}")


def structured_document(
    *, body: str = "# Product\n\nUseful product information " * 8
) -> StructuredWebDocument:
    return StructuredWebDocument(
        tenant_id="tenant-a",
        site_id="site-a",
        document_id="document-a",
        canonical_url="https://shop.example.com/product.html",
        title="Product",
        language="en",
        body=body,
        content_hash="hash-a",
        category="product",
        product={"name": "Product", "sku": "SKU-100"},
        internal_links=(),
        fetched_at=datetime.now(UTC),
        source_metadata={"source_type": "website_html"},
    )


def test_canonicalizer_removes_tracking_fragments_and_rejects_other_hosts() -> None:
    canonical = canonicalize_url(
        "../products/item.html?utm_source=email&color=red#details",
        base_url="https://shop.example.com/catalog/",
        allowed_hosts=frozenset({"shop.example.com"}),
    )

    assert canonical == "https://shop.example.com/products/item.html?color=red"
    assert canonicalize_url(
        "https://shop.example.com/custom-map?source=partner&utm_source=feed&sig=b%2Fa",
        allowed_hosts=frozenset({"shop.example.com"}),
        keep_xml=True,
        preserve_query=True,
    ) == ("https://shop.example.com/custom-map?source=partner&utm_source=feed&sig=b%2Fa")
    with pytest.raises(ValueError, match="crawl boundary"):
        canonicalize_url(
            "https://other.example.com/item.html",
            allowed_hosts=frozenset({"shop.example.com"}),
        )


def test_canonicalizer_preserves_brackets_for_ipv6_origins() -> None:
    url = "https://[2606:4700:4700::1111]/sitemap.xml"

    assert (
        canonicalize_url(
            url,
            allowed_hosts=frozenset({"2606:4700:4700::1111"}),
            keep_xml=True,
        )
        == url
    )
    assert origin_for_url(url) == "https://[2606:4700:4700::1111]"


def test_public_fetch_validation_blocks_private_dns(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        "app.knowledge.web.html_fetcher.socket.getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("127.0.0.1", 0))],
    )

    with pytest.raises(PermissionError, match="non-public"):
        _validate_public_url(
            "https://shop.example.com/",
            frozenset({"shop.example.com"}),
        )


def test_public_fetch_validation_enforces_exact_origin(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        "app.knowledge.web.html_fetcher.socket.getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("93.184.216.34", 0))],
    )
    allowed_origins = frozenset({"https://shop.example.com"})

    assert (
        _validate_public_url(
            "https://shop.example.com/sitemap.xml",
            frozenset({"shop.example.com"}),
            allowed_origins,
        )
        == "https://shop.example.com/sitemap.xml"
    )
    with pytest.raises(PermissionError, match="origin"):
        _validate_public_url(
            "http://shop.example.com/sitemap.xml",
            frozenset({"shop.example.com"}),
            allowed_origins,
        )
    with pytest.raises(PermissionError, match="origin"):
        _validate_public_url(
            "https://shop.example.com:8443/sitemap.xml",
            frozenset({"shop.example.com"}),
            allowed_origins,
        )


def test_pinned_connection_revalidates_and_rejects_dns_rebinding(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    answers = iter(
        (
            [(2, 1, 6, "", ("93.184.216.34", 443))],
            [(2, 1, 6, "", ("127.0.0.1", 443))],
        )
    )
    monkeypatch.setattr(
        "app.knowledge.web.html_fetcher.socket.getaddrinfo",
        lambda *args, **kwargs: next(answers),
    )
    monkeypatch.setattr(
        "app.knowledge.web.html_fetcher.socket.socket",
        lambda *args, **kwargs: pytest.fail("private rebinding target must not open a socket"),
    )

    assert (
        _validate_public_url(
            "https://shop.example.com/sitemap.xml",
            frozenset({"shop.example.com"}),
        )
        == "https://shop.example.com/sitemap.xml"
    )
    with pytest.raises(PermissionError, match="non-public"):
        _create_public_connection(("shop.example.com", 443), timeout=10)


def test_pinned_connection_uses_the_validated_socket_address(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    connected: list[tuple[str, int]] = []

    class FakeSocket:
        def settimeout(self, timeout: float) -> None:
            assert timeout == 10

        def bind(self, source_address: tuple[str, int]) -> None:
            raise AssertionError(f"unexpected source binding: {source_address}")

        def connect(self, socket_address: tuple[str, int]) -> None:
            connected.append(socket_address)

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        "app.knowledge.web.html_fetcher.socket.getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )
    monkeypatch.setattr(
        "app.knowledge.web.html_fetcher.socket.socket",
        lambda *args, **kwargs: FakeSocket(),
    )

    connection = _create_public_connection(("shop.example.com", 443), timeout=10)

    assert isinstance(connection, FakeSocket)
    assert connected == [("93.184.216.34", 443)]


def test_safe_http_fetcher_sends_validators_and_accepts_empty_304(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    opener = CapturingHttpOpener()
    monkeypatch.setattr(
        "app.knowledge.web.html_fetcher._build_opener",
        lambda hosts, **kwargs: opener,
    )
    monkeypatch.setattr(
        "app.knowledge.web.html_fetcher._validate_public_url",
        lambda url, hosts, origins=frozenset(), preserve_query=False: url,
    )

    result = SafeHttpFetcher()._fetch_sync(
        WebFetchRequest(
            url="https://shop.example.com/products/example.html",
            allowed_hosts=frozenset({"shop.example.com"}),
            if_none_match='"product-v1"',
            if_modified_since="Sun, 27 Jul 2026 00:00:00 GMT",
        )
    )

    assert opener.request is not None
    headers = {key.casefold(): value for key, value in opener.request.header_items()}
    assert headers["if-none-match"] == '"product-v1"'
    assert headers["if-modified-since"] == "Sun, 27 Jul 2026 00:00:00 GMT"
    assert result.status_code == 304
    assert result.body == b""


def test_safe_http_fetcher_identifies_unsupported_content_type(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    opener = CapturingHttpOpener()
    monkeypatch.setattr(opener, "open", lambda request, timeout: UnsupportedContentHttpResponse())
    monkeypatch.setattr(
        "app.knowledge.web.html_fetcher._build_opener",
        lambda hosts, **kwargs: opener,
    )
    monkeypatch.setattr(
        "app.knowledge.web.html_fetcher._validate_public_url",
        lambda url, hosts, origins=frozenset(), preserve_query=False: url,
    )

    with pytest.raises(UnsupportedWebContentTypeError) as raised:
        SafeHttpFetcher()._fetch_sync(
            WebFetchRequest(
                url="https://shop.example.com/catalog.pdf",
                allowed_hosts=frozenset({"shop.example.com"}),
                accepted_content_types=frozenset({"text/html"}),
            )
        )

    assert raised.value.content_type == "application/pdf"


def test_safe_http_fetcher_can_defer_sitemap_mime_validation_to_the_parser(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    opener = CapturingHttpOpener()
    monkeypatch.setattr(
        opener,
        "open",
        lambda request, timeout: ArbitrarySitemapContentHttpResponse(),
    )
    monkeypatch.setattr(
        "app.knowledge.web.html_fetcher._build_opener",
        lambda hosts, **kwargs: opener,
    )
    monkeypatch.setattr(
        "app.knowledge.web.html_fetcher._validate_public_url",
        lambda url, hosts, origins=frozenset(), preserve_query=False: url,
    )

    result = SafeHttpFetcher()._fetch_sync(
        WebFetchRequest(
            url="https://shop.example.com/custom-map",
            allowed_hosts=frozenset({"shop.example.com"}),
            accepted_content_types=frozenset({"*"}),
        )
    )

    assert SitemapParser().parse(result.body).pages == ()


def test_safe_http_fetcher_preserves_retry_headers_for_plain_text_errors(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    opener = CapturingHttpOpener()
    monkeypatch.setattr(opener, "open", lambda request, timeout: RetryableHttpResponse())
    monkeypatch.setattr(
        "app.knowledge.web.html_fetcher._build_opener",
        lambda hosts, **kwargs: opener,
    )
    monkeypatch.setattr(
        "app.knowledge.web.html_fetcher._validate_public_url",
        lambda url, hosts, origins=frozenset(), preserve_query=False: url,
    )

    result = SafeHttpFetcher()._fetch_sync(
        WebFetchRequest(
            url="https://shop.example.com/products/example.html",
            allowed_hosts=frozenset({"shop.example.com"}),
            accepted_content_types=frozenset({"text/html"}),
        )
    )

    assert result.status_code == 429
    assert result.body == b""
    assert result.headers["retry-after"] == "60"


def test_sitemap_parser_supports_urlsets_and_rejects_entities() -> None:
    parsed = SitemapParser().parse(
        b"""<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
        <url><loc>https://shop.example.com/a.html</loc><lastmod>2026-07-17</lastmod></url>
        </urlset>"""
    )

    assert parsed.pages[0].url == "https://shop.example.com/a.html"
    assert parsed.pages[0].last_modified == "2026-07-17"
    with pytest.raises(ValueError, match="prohibited"):
        SitemapParser().parse(
            b"<!DOCTYPE foo [<!ENTITY xxe SYSTEM 'file:///etc/passwd'>]><urlset/>"
        )


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_sitemap_parser_rejects_late_doctype_declarations_in_any_supported_encoding(
    encoding: str,
) -> None:
    document = (
        f"<?xml version='1.0' encoding='{encoding}'?>"
        + (" " * 5000)
        + "<!DOCTYPE urlset [<!ENTITY target 'https://shop.example.com/a'>]>"
        + "<urlset><url><loc>&target;</loc></url></urlset>"
    ).encode(encoding)

    with pytest.raises(ValueError, match="prohibited"):
        SitemapParser().parse(document)


def test_gtranslate_language_scope_matches_only_the_first_path_segment() -> None:
    assert is_translated_url("https://shop.example.com/de/product.html", ("de",))
    assert is_translated_url("https://shop.example.com/pt-br/product.html", ("pt-br",))
    assert not is_translated_url("https://shop.example.com/design/product.html", ("de",))
    assert not is_translated_url("https://shop.example.com/product-de.html", ("de",))


def test_gtranslate_sitemap_classification_requires_sibling_evidence() -> None:
    urls = (
        "https://shop.example.com/sitemap-products.xml",
        "https://shop.example.com/sitemap-products-de.xml",
        "https://shop.example.com/sitemap-products-pt-br.xml",
        "https://shop.example.com/sitemap-map.xml",
    )

    classifications, locales = classify_sitemap_locales(urls, primary_language="en")

    assert classifications[urls[0]] is None
    assert classifications[urls[1]] == "de"
    assert classifications[urls[2]] == "pt-br"
    assert classifications[urls[3]] is None
    assert locales == ("de", "pt-br")


async def test_preflight_does_not_fetch_gtranslate_sitemaps_or_include_language_paths() -> None:
    root = "https://shop.example.com/sitemap.xml"
    products = "https://shop.example.com/sitemap-products.xml"
    translated_de = "https://shop.example.com/sitemap-products-de.xml"
    translated_pt = "https://shop.example.com/sitemap-products-pt-br.xml"
    fetcher = FakeWebFetcher(
        {
            root: response(
                root,
                (
                    "<?xml version='1.0'?><sitemapindex>"
                    f"<sitemap><loc>{products}</loc></sitemap>"
                    f"<sitemap><loc>{translated_de}</loc></sitemap>"
                    f"<sitemap><loc>{translated_pt}</loc></sitemap>"
                    "</sitemapindex>"
                ).encode(),
                "application/xml",
            ),
            products: response(
                products,
                (
                    b"<?xml version='1.0'?><urlset>"
                    b"<url><loc>https://shop.example.com/product.html</loc></url>"
                    b"<url><loc>https://shop.example.com/de/product.html</loc></url>"
                    b"<url><loc>https://shop.example.com/design/product.html</loc></url>"
                    b"<url><loc>https://shop.example.com/llms.txt</loc></url>"
                    b"<url><loc>https://shop.example.com/agents.md</loc></url>"
                    b"<url><loc>https://shop.example.com/.well-known/ucp</loc></url>"
                    b"</urlset>"
                ),
                "application/xml",
            ),
        }
    )

    inspection = await GTranslateWebCrawlPreflightInspector(fetcher=fetcher).inspect(
        WebCrawlInspectionRequest(
            base_url="https://shop.example.com",
            primary_language="en",
            translation_provider="gtranslate",
            max_sitemaps=10,
            max_response_bytes=1_000_000,
            request_timeout_seconds=10,
            user_agent="test-crawler",
        )
    )

    requested_urls = {request.url for request in fetcher.requests}
    assert translated_de not in requested_urls
    assert translated_pt not in requested_urls
    assert inspection.translated_locales == ("de", "pt-br")
    assert inspection.excluded_sitemap_count == 2
    assert inspection.excluded_url_count == 4
    assert {item.url for item in inspection.items} == {
        "https://shop.example.com/product.html",
        "https://shop.example.com/design/product.html",
    }


async def test_preflight_falls_back_from_soft_html_to_sitemap_index() -> None:
    base_url = "https://shop.example.com"
    default_root = f"{base_url}/sitemap.xml"
    index_root = f"{base_url}/sitemap_index.xml"
    products = f"{base_url}/product-sitemap.xml"
    fetcher = FakeWebFetcher(
        {
            default_root: response(
                default_root,
                b"<!doctype html><html><body>Storefront</body></html>",
                "text/html",
            ),
            index_root: response(
                index_root,
                (
                    "<?xml version='1.0'?><sitemapindex>"
                    f"<sitemap><loc>{products}</loc></sitemap>"
                    "</sitemapindex>"
                ).encode(),
                "application/xml",
            ),
            products: response(
                products,
                (
                    "<?xml version='1.0'?><urlset>"
                    f"<url><loc>{base_url}/product.html</loc></url>"
                    "</urlset>"
                ).encode(),
                "application/xml",
            ),
        }
    )

    inspection = await GTranslateWebCrawlPreflightInspector(fetcher=fetcher).inspect(
        WebCrawlInspectionRequest(
            base_url=base_url,
            primary_language="en",
            translation_provider="gtranslate",
            max_sitemaps=10,
            max_response_bytes=1_000_000,
            max_decompressed_response_bytes=2_000_000,
            max_compression_ratio=25,
            request_timeout_seconds=10,
            user_agent="test-crawler",
        )
    )

    assert inspection.blocking_reasons == ()
    assert inspection.root_sitemap_url == index_root
    assert inspection.root_sitemap_urls == (index_root,)
    assert inspection.discovery_method == "common_path"
    assert {item.url for item in inspection.items} == {f"{base_url}/product.html"}
    index_request = next(request for request in fetcher.requests if request.url == index_root)
    assert index_request.max_response_bytes == 1_000_000
    assert index_request.max_decompressed_bytes == 2_000_000
    assert index_request.max_compression_ratio == 25
    assert index_request.allowed_origins == frozenset({base_url})
    assert any(
        attempt.url == default_root and attempt.outcome == "invalid_sitemap"
        for attempt in inspection.discovery_attempts
    )


async def test_preflight_combines_all_valid_common_sitemap_roots() -> None:
    base_url = "https://shop.example.com"
    leaf_root = f"{base_url}/sitemap.xml"
    index_root = f"{base_url}/sitemap_index.xml"
    product_map = f"{base_url}/products.xml"
    fetcher = FakeWebFetcher(
        {
            leaf_root: response(
                leaf_root,
                (
                    "<?xml version='1.0'?><urlset>"
                    f"<url><loc>{base_url}/policy.html</loc></url>"
                    "</urlset>"
                ).encode(),
                "application/xml",
            ),
            index_root: response(
                index_root,
                (
                    "<?xml version='1.0'?><sitemapindex>"
                    f"<sitemap><loc>{product_map}</loc></sitemap>"
                    "</sitemapindex>"
                ).encode(),
                "application/xml",
            ),
            product_map: response(
                product_map,
                (
                    "<?xml version='1.0'?><urlset>"
                    f"<url><loc>{base_url}/product.html</loc></url>"
                    "</urlset>"
                ).encode(),
                "application/xml",
            ),
        }
    )

    inspection = await GTranslateWebCrawlPreflightInspector(fetcher=fetcher).inspect(
        WebCrawlInspectionRequest(
            base_url=base_url,
            primary_language="en",
            translation_provider="gtranslate",
            max_sitemaps=10,
            max_response_bytes=1_000_000,
            request_timeout_seconds=10,
            user_agent="test-crawler",
        )
    )

    assert inspection.blocking_reasons == ()
    assert inspection.root_sitemap_urls == (leaf_root, index_root)
    assert {item.url for item in inspection.items} == {
        f"{base_url}/policy.html",
        f"{base_url}/product.html",
    }


async def test_preflight_deduplicates_nested_sitemaps_before_applying_the_limit() -> None:
    base_url = "https://shop.example.com"
    root = f"{base_url}/custom-index.xml"
    products = f"{base_url}/products.xml"
    fetcher = FakeWebFetcher(
        {
            root: response(
                root,
                (
                    "<?xml version='1.0'?><sitemapindex>"
                    f"<sitemap><loc>{products}</loc></sitemap>"
                    f"<sitemap><loc>{products}</loc></sitemap>"
                    "</sitemapindex>"
                ).encode(),
                "application/xml",
            ),
            products: response(
                products,
                (
                    "<?xml version='1.0'?><urlset>"
                    f"<url><loc>{base_url}/product.html</loc></url>"
                    "</urlset>"
                ).encode(),
                "application/xml",
            ),
        }
    )

    inspection = await GTranslateWebCrawlPreflightInspector(fetcher=fetcher).inspect(
        WebCrawlInspectionRequest(
            base_url=base_url,
            primary_language="en",
            translation_provider="gtranslate",
            max_sitemaps=2,
            max_response_bytes=1_000_000,
            request_timeout_seconds=10,
            user_agent="test-crawler",
            discovery_mode="manual",
            explicit_sitemap_urls=(root,),
        )
    )

    assert inspection.blocking_reasons == ()
    assert [request.url for request in fetcher.requests].count(products) == 1
    assert [item.url for item in inspection.items] == [f"{base_url}/product.html"]


async def test_preflight_discovers_origin_root_sitemap_for_a_path_based_site_url() -> None:
    base_url = "https://shop.example.com/storefront"
    root = "https://shop.example.com/sitemap.xml"
    fetcher = FakeWebFetcher(
        {
            root: response(
                root,
                (
                    b"<?xml version='1.0'?><urlset>"
                    b"<url><loc>https://shop.example.com/product.html</loc></url>"
                    b"</urlset>"
                ),
                "application/xml",
            )
        }
    )

    inspection = await GTranslateWebCrawlPreflightInspector(fetcher=fetcher).inspect(
        WebCrawlInspectionRequest(
            base_url=base_url,
            primary_language="en",
            translation_provider="gtranslate",
            max_sitemaps=10,
            max_response_bytes=1_000_000,
            request_timeout_seconds=10,
            user_agent="test-crawler",
        )
    )

    assert inspection.root_sitemap_urls == (root,)
    assert fetcher.requests[0].url == "https://shop.example.com/robots.txt"
    assert root in {request.url for request in fetcher.requests}


async def test_preflight_uses_custom_sitemap_declared_by_robots() -> None:
    base_url = "https://shop.example.com"
    robots_url = f"{base_url}/robots.txt"
    custom_root = f"{base_url}/catalog-map?source=seo&utm_source=feed&sig=b%2Fa"
    fetcher = FakeWebFetcher(
        {
            robots_url: response(
                robots_url,
                f"User-agent: *\nSitemap: {custom_root}\n".encode(),
                "text/plain",
            ),
            custom_root: response(
                custom_root,
                (
                    "<?xml version='1.0'?><urlset>"
                    f"<url><loc>{base_url}/custom-product.html</loc></url>"
                    "</urlset>"
                ).encode(),
                "application/octet-stream",
            ),
        }
    )

    inspection = await GTranslateWebCrawlPreflightInspector(fetcher=fetcher).inspect(
        WebCrawlInspectionRequest(
            base_url=base_url,
            primary_language="en",
            translation_provider="gtranslate",
            max_sitemaps=10,
            max_response_bytes=1_000_000,
            request_timeout_seconds=10,
            user_agent="test-crawler",
            discovery_mode="auto",
        )
    )

    assert inspection.blocking_reasons == ()
    assert inspection.discovery_method == "robots_txt"
    assert inspection.root_sitemap_urls == (custom_root,)
    assert inspection.items[0].url == f"{base_url}/custom-product.html"


async def test_manual_only_preflight_does_not_probe_automatic_candidates() -> None:
    base_url = "https://shop.example.com"
    custom_root = f"{base_url}/custom-map.xml"
    fetcher = FakeWebFetcher({custom_root: ConnectionError("unavailable")})

    inspection = await GTranslateWebCrawlPreflightInspector(fetcher=fetcher).inspect(
        WebCrawlInspectionRequest(
            base_url=base_url,
            primary_language="en",
            translation_provider="gtranslate",
            max_sitemaps=10,
            max_response_bytes=1_000_000,
            request_timeout_seconds=10,
            user_agent="test-crawler",
            discovery_mode="manual",
            explicit_sitemap_urls=(custom_root,),
        )
    )

    assert inspection.blocking_reasons == (
        "sitemap_not_discovered",
        "no_primary_language_urls",
    )
    assert [request.url for request in fetcher.requests] == [custom_root]


async def test_hybrid_preflight_warns_when_a_configured_origin_is_no_longer_verified() -> None:
    base_url = "https://shop.example.com"
    stale_root = "https://old-maps.example-cdn.com/sitemap.xml"
    discovered_root = f"{base_url}/sitemap.xml"
    fetcher = FakeWebFetcher(
        {
            discovered_root: response(
                discovered_root,
                (
                    "<?xml version='1.0'?><urlset>"
                    f"<url><loc>{base_url}/product.html</loc></url>"
                    "</urlset>"
                ).encode(),
                "application/xml",
            )
        }
    )

    inspection = await GTranslateWebCrawlPreflightInspector(fetcher=fetcher).inspect(
        WebCrawlInspectionRequest(
            base_url=base_url,
            primary_language="en",
            translation_provider="gtranslate",
            max_sitemaps=10,
            max_response_bytes=1_000_000,
            request_timeout_seconds=10,
            user_agent="test-crawler",
            discovery_mode="hybrid",
            explicit_sitemap_urls=(stale_root,),
        )
    )

    assert inspection.discovery_method == "common_path"
    assert inspection.warnings == ("configured_sitemap_fallback_used",)
    assert stale_root not in {request.url for request in fetcher.requests}


async def test_preflight_retries_a_transient_nested_sitemap_failure_once() -> None:
    base_url = "https://shop.example.com"
    root = f"{base_url}/custom-index.xml"
    products = f"{base_url}/product-map.xml"
    fetcher = FakeWebFetcher(
        {
            root: response(
                root,
                (
                    "<?xml version='1.0'?><sitemapindex>"
                    f"<sitemap><loc>{products}</loc></sitemap>"
                    "</sitemapindex>"
                ).encode(),
                "application/xml",
            ),
            products: [
                ConnectionError("temporary CDN failure"),
                response(
                    products,
                    (
                        "<?xml version='1.0'?><urlset>"
                        f"<url><loc>{base_url}/product.html</loc></url>"
                        "</urlset>"
                    ).encode(),
                    "application/xml",
                ),
            ],
        }
    )

    inspection = await GTranslateWebCrawlPreflightInspector(fetcher=fetcher).inspect(
        WebCrawlInspectionRequest(
            base_url=base_url,
            primary_language="en",
            translation_provider="gtranslate",
            max_sitemaps=10,
            max_response_bytes=1_000_000,
            request_timeout_seconds=10,
            user_agent="test-crawler",
            discovery_mode="manual",
            explicit_sitemap_urls=(root,),
        )
    )

    assert inspection.blocking_reasons == ()
    assert inspection.warnings == ("sitemap_retry_recovered",)
    assert [request.url for request in fetcher.requests].count(products) == 2
    assert {attempt.outcome for attempt in inspection.discovery_attempts} >= {
        "retrying_fetch_failed",
        "accepted",
    }


async def test_preflight_allows_verified_cross_origin_sitemaps_but_not_their_pages() -> None:
    base_url = "https://shop.example.com"
    root = "https://maps.example-cdn.com/catalog-index.xml"
    products = "https://maps-backup.example-cdn.com/products.xml"
    fetcher = FakeWebFetcher(
        {
            root: response(
                root,
                (
                    "<?xml version='1.0'?><sitemapindex>"
                    f"<sitemap><loc>{products}</loc></sitemap>"
                    "</sitemapindex>"
                ).encode(),
                "application/xml",
            ),
            products: response(
                products,
                (
                    "<?xml version='1.0'?><urlset>"
                    f"<url><loc>{base_url}/product.html</loc></url>"
                    f"<url><loc>{products.rsplit('/', 1)[0]}/must-not-cross.html</loc></url>"
                    "</urlset>"
                ).encode(),
                "application/xml",
            ),
        }
    )

    inspection = await GTranslateWebCrawlPreflightInspector(fetcher=fetcher).inspect(
        WebCrawlInspectionRequest(
            base_url=base_url,
            primary_language="en",
            translation_provider="gtranslate",
            max_sitemaps=10,
            max_response_bytes=1_000_000,
            request_timeout_seconds=10,
            user_agent="test-crawler",
            discovery_mode="manual",
            explicit_sitemap_urls=(root,),
            allowed_sitemap_origins=(
                "https://maps.example-cdn.com",
                "https://maps-backup.example-cdn.com",
            ),
        )
    )

    assert inspection.blocking_reasons == ()
    assert inspection.root_sitemap_urls == (root,)
    assert inspection.primary_sitemap_urls == (products,)
    assert [item.url for item in inspection.items] == [f"{base_url}/product.html"]
    assert {request.url for request in fetcher.requests} == {root, products}
    expected_origins = frozenset(
        {
            base_url,
            "https://maps.example-cdn.com",
            "https://maps-backup.example-cdn.com",
        }
    )
    assert all(request.allowed_origins == expected_origins for request in fetcher.requests)


async def test_preflight_never_fetches_an_unverified_cross_origin_child_sitemap() -> None:
    base_url = "https://shop.example.com"
    root = "https://maps.example-cdn.com/catalog-index.xml"
    unverified = "https://unverified.example-cdn.com/products.xml"
    fetcher = FakeWebFetcher(
        {
            root: response(
                root,
                (
                    "<?xml version='1.0'?><sitemapindex>"
                    f"<sitemap><loc>{unverified}</loc></sitemap>"
                    "</sitemapindex>"
                ).encode(),
                "application/xml",
            ),
            unverified: AssertionError("unverified sitemap must not be fetched"),
        }
    )

    inspection = await GTranslateWebCrawlPreflightInspector(fetcher=fetcher).inspect(
        WebCrawlInspectionRequest(
            base_url=base_url,
            primary_language="en",
            translation_provider="gtranslate",
            max_sitemaps=10,
            max_response_bytes=1_000_000,
            request_timeout_seconds=10,
            user_agent="test-crawler",
            discovery_mode="manual",
            explicit_sitemap_urls=(root,),
            allowed_sitemap_origins=("https://maps.example-cdn.com",),
        )
    )

    assert "untrusted_or_invalid_sitemap_url" in inspection.blocking_reasons
    assert [request.url for request in fetcher.requests] == [root]


async def test_preflight_blocks_and_stops_fetching_when_global_url_limit_is_exceeded() -> None:
    base_url = "https://shop.example.com"
    root = f"{base_url}/sitemap.xml"
    first_nested = f"{base_url}/products-1.xml"
    second_nested = f"{base_url}/products-2.xml"
    fetcher = FakeWebFetcher(
        {
            root: response(
                root,
                (
                    "<?xml version='1.0'?><sitemapindex>"
                    f"<sitemap><loc>{first_nested}</loc></sitemap>"
                    f"<sitemap><loc>{second_nested}</loc></sitemap>"
                    "</sitemapindex>"
                ).encode(),
                "application/xml",
            ),
            first_nested: response(
                first_nested,
                (
                    "<?xml version='1.0'?><urlset>"
                    f"<url><loc>{base_url}/product-1.html</loc></url>"
                    f"<url><loc>{base_url}/product-2.html</loc></url>"
                    f"<url><loc>{base_url}/product-3.html</loc></url>"
                    "</urlset>"
                ).encode(),
                "application/xml",
            ),
            second_nested: response(
                second_nested,
                (
                    "<?xml version='1.0'?><urlset>"
                    f"<url><loc>{base_url}/must-not-be-fetched.html</loc></url>"
                    "</urlset>"
                ).encode(),
                "application/xml",
            ),
        }
    )

    inspection = await GTranslateWebCrawlPreflightInspector(fetcher=fetcher).inspect(
        WebCrawlInspectionRequest(
            base_url=base_url,
            primary_language="en",
            translation_provider="gtranslate",
            max_sitemaps=10,
            max_response_bytes=1_000_000,
            request_timeout_seconds=10,
            user_agent="test-crawler",
            max_urls=2,
        )
    )

    assert inspection.blocking_reasons == ("manifest_url_limit_reached",)
    assert inspection.coverage_status == "incomplete"
    assert [item.url for item in inspection.items] == [
        f"{base_url}/product-1.html",
        f"{base_url}/product-2.html",
    ]
    assert second_nested not in {request.url for request in fetcher.requests}


async def test_preflight_url_limit_counts_unique_urls() -> None:
    base_url = "https://shop.example.com"
    root = f"{base_url}/sitemap.xml"
    fetcher = FakeWebFetcher(
        {
            root: response(
                root,
                (
                    "<?xml version='1.0'?><urlset>"
                    f"<url><loc>{base_url}/product.html</loc></url>"
                    f"<url><loc>{base_url}/product.html</loc></url>"
                    "</urlset>"
                ).encode(),
                "application/xml",
            )
        }
    )

    inspection = await GTranslateWebCrawlPreflightInspector(fetcher=fetcher).inspect(
        WebCrawlInspectionRequest(
            base_url=base_url,
            primary_language="en",
            translation_provider="gtranslate",
            max_sitemaps=10,
            max_response_bytes=1_000_000,
            request_timeout_seconds=10,
            user_agent="test-crawler",
            max_urls=1,
        )
    )

    assert inspection.blocking_reasons == ()
    assert [item.url for item in inspection.items] == [f"{base_url}/product.html"]


class StaticPreflightInspector:
    def __init__(self, inspection: WebCrawlInspection) -> None:
        self.inspection = inspection
        self.last_request: WebCrawlInspectionRequest | None = None

    async def inspect(self, request: WebCrawlInspectionRequest) -> WebCrawlInspection:
        self.last_request = request
        return self.inspection


async def test_new_preflight_manifest_carries_forward_same_site_validators() -> None:
    now = datetime.now(UTC)
    url = "https://shop.example.com/product.html"
    previous = WebCrawlManifest(
        tenant_id="tenant-a",
        site_id="site-a",
        manifest_id="manifest-old",
        base_url="https://shop.example.com",
        root_sitemap_url="https://shop.example.com/sitemap.xml",
        primary_language="en",
        translation_provider="gtranslate",
        status=WebCrawlManifestStatus.READY,
        fingerprint="a" * 64,
        primary_sitemap_urls=("https://shop.example.com/sitemap-products.xml",),
        translated_locales=("de",),
        excluded_sitemap_count=1,
        excluded_url_count=0,
        blocking_reasons=(),
        created_by="admin-1",
        created_at=now,
        items=(
            WebCrawlManifestItem(
                url=url,
                source_sitemap_url="https://shop.example.com/sitemap-products.xml",
                last_modified="sitemap-old",
                document_id="document-1",
                version_id="version-1",
                canonical_url=url,
                final_url=url,
                etag='"etag-v1"',
                response_last_modified="Sun, 27 Jul 2026 00:00:00 GMT",
                product_key="SKU-100",
                artifact_status="published",
                validated_at=now,
            ),
        ),
    )
    store = InMemoryWebCrawlManifestStore((previous,))
    inspector = StaticPreflightInspector(
        WebCrawlInspection(
            root_sitemap_url="https://shop.example.com/sitemap.xml",
            primary_sitemap_urls=("https://shop.example.com/sitemap-products.xml",),
            translated_locales=("de",),
            excluded_sitemap_count=1,
            excluded_url_count=0,
            blocking_reasons=(),
            items=(
                WebCrawlManifestItem(
                    url=url,
                    source_sitemap_url="https://shop.example.com/sitemap-products.xml",
                    last_modified="sitemap-new",
                ),
            ),
        )
    )
    service = WebCrawlPreflightService(
        inspector=inspector,
        store=store,
        runtime_policy=WebCrawlPreflightRuntimePolicy(10, 1_000_000, 10, max_urls=123),
    )
    principal = AuthenticatedPrincipal(
        subject_id="admin-1",
        tenant_id="tenant-a",
        roles=frozenset({"knowledge_admin"}),
        scopes=frozenset({"knowledge:read", "knowledge:sync"}),
        authentication_method="session",
        authenticated_at=now,
        correlation_id="correlation-1",
    )

    result = await service.run(
        RunWebCrawlPreflightCommand(
            principal=principal,
            site_id="site-a",
            base_url="https://shop.example.com",
            primary_language="en",
            allowed_sitemap_origins=("https://maps.example-cdn.com",),
        )
    )

    item = result.manifest.items[0]
    assert result.manifest.version == 2
    assert result.manifest.url_count == 1
    assert result.manifest.content_kind_counts == {"general": 1}
    assert item.last_modified == "sitemap-new"
    assert item.document_id == "document-1"
    assert item.version_id == "version-1"
    assert item.etag == '"etag-v1"'
    assert item.product_key == "SKU-100"
    assert inspector.last_request is not None
    assert inspector.last_request.max_urls == 123
    assert inspector.last_request.allowed_sitemap_origins == ("https://maps.example-cdn.com",)


def test_html_parser_and_extractor_remove_noise_and_keep_product_structure() -> None:
    parser = HtmlKnowledgeParser()
    page = parser.parse(
        requested_url="https://shop.example.com/products/example.html",
        final_url="https://shop.example.com/products/example.html",
        body=PRODUCT_HTML,
        content_type="text/html; charset=utf-8",
        allowed_hosts=frozenset({"shop.example.com"}),
    )
    product = ProductExtractor().extract(page)
    document = StructuredContentExtractor().extract(
        page=page,
        tenant_id="tenant-a",
        site_id="site-a",
        fetched_at=datetime.now(UTC),
        product=product,
    )

    assert document.canonical_url == "https://shop.example.com/products/example.html"
    assert document.product is not None
    assert document.product["sku"] == "SKU-100"
    assert document.product["properties"]["Height"] == "125cm"
    assert document.product["properties"]["Weight"] == "20kg"
    assert "Structured product data" in document.body
    assert "Weight | 20kg" in document.body
    assert "discreet packaging" in document.body
    assert "Shipping Notes" in document.body
    assert "Order processing takes about 3-7 days." in document.body
    assert "Shipping takes about 7-20 days." in document.body
    assert "analytics script" not in document.body
    assert "Accept cookies" not in document.body
    assert "Copyright" not in document.body
    assert "private form value" not in document.body
    assert "<script" not in document.body
    assert page.internal_links == ("https://shop.example.com/faq.html",)
    assert document.source_metadata["parsed_block_count"] >= 2
    assert document.source_metadata["freshness_class"] == "volatile"
    assert document.source_metadata["max_age_seconds"] == 900


def test_extractor_downgrades_oversized_and_low_level_headings_without_losing_text() -> None:
    long_heading = "L" * 2000
    recommendation = "R" * 800
    html = f"""
    <html><head><title>Metadata Contract</title></head><body><main>
      <h1>Metadata Contract</h1>
      <h2>Ｓｉｚｅ\u00a0Guide\u0000</h2>
      <p>This paragraph provides enough useful page content for extraction and indexing.</p>
      <h2>{long_heading}</h2>
      <h4>Implementation detail heading</h4>
      <div class="related-products"><h2>{recommendation}</h2></div>
    </main></body></html>
    """.encode()
    page = HtmlKnowledgeParser().parse(
        requested_url="https://shop.example.com/metadata.html",
        final_url="https://shop.example.com/metadata.html",
        body=html,
        content_type="text/html; charset=utf-8",
        allowed_hosts=frozenset({"shop.example.com"}),
    )

    document = StructuredContentExtractor().extract(
        page=page,
        tenant_id="tenant-a",
        site_id="site-a",
        fetched_at=datetime.now(UTC),
        product=None,
    )
    chunks = MarkdownChunker().chunk(
        ParsedKnowledgeDocument(
            path=Path("metadata.md"),
            metadata={"document_id": document.document_id},
            body=document.body,
            internal_links=(),
            content_hash=document.content_hash,
        )
    )

    assert "## Size Guide" in document.body
    assert long_heading in document.body
    assert f"## {long_heading}" not in document.body
    assert "Implementation detail heading" in document.body
    assert recommendation not in document.body
    assert document.source_metadata["heading_rejected_count"] == 2
    assert all(chunk.heading is None or len(chunk.heading) <= 240 for chunk in chunks)


async def test_crawler_excludes_utility_page_with_valueless_meta_content() -> None:
    utility_url = "https://shop.example.com/product-size-converter.html"
    fetcher = FakeWebFetcher(
        {
            utility_url: response(
                utility_url,
                b'<html lang="en"><head><meta name="description" content></head>'
                b"<body><main></main></body></html>",
                "text/html; charset=utf-8",
            )
        }
    )

    result = await WebsiteKnowledgeCrawler(fetcher=fetcher).crawl(
        WebCrawlPolicy(
            tenant_id="tenant-a",
            site_id="site-a",
            base_url="https://shop.example.com/",
            seed_urls=(utility_url,),
            max_pages=1,
            crawl_delay_seconds=0,
            follow_internal_links=False,
            respect_robots_txt=False,
            discover_sitemaps=False,
            content_kind="utility",
        )
    )

    assert result.documents == ()
    assert result.excluded_count == 1
    assert result.failed_count == 0
    assert result.errors[utility_url] == (
        "excluded: approved_utility_page_without_knowledge_content"
    )


async def test_crawler_preserves_detected_guide_category_page() -> None:
    category_url = "https://shop.example.com/guides/accessibility/"
    fetcher = FakeWebFetcher(
        {
            category_url: response(
                category_url,
                b"""
                <html lang="en"><head>
                  <title>Accessibility Guides</title>
                  <meta name="description" content="Inclusive companionship resources.">
                  <script type="application/ld+json">
                    {"@context":"https://schema.org","@type":"CollectionPage"}
                  </script>
                </head><body><main><article>
                  <h1>Accessibility Guides</h1>
                  <p>Browse practical accessibility and therapeutic companionship guidance.</p>
                </article></main></body></html>
                """,
                "text/html; charset=utf-8",
            )
        }
    )

    result = await WebsiteKnowledgeCrawler(fetcher=fetcher).crawl(
        WebCrawlPolicy(
            tenant_id="tenant-a",
            site_id="site-a",
            base_url="https://shop.example.com/",
            seed_urls=(category_url,),
            max_pages=1,
            crawl_delay_seconds=0,
            follow_internal_links=False,
            respect_robots_txt=False,
            discover_sitemaps=False,
            content_kind="guide",
        )
    )

    assert result.failed_count == 0
    assert result.excluded_count == 0
    assert result.documents[0].category == "category"
    assert result.documents[0].source_metadata["content_kind"] == "category"


def test_web_document_and_chunk_ids_are_namespaced_by_tenant_and_site() -> None:
    page = HtmlKnowledgeParser().parse(
        requested_url="https://shop.example.com/products/example.html",
        final_url="https://shop.example.com/products/example.html",
        body=PRODUCT_HTML,
        content_type="text/html; charset=utf-8",
        allowed_hosts=frozenset({"shop.example.com"}),
    )
    extractor = StructuredContentExtractor()
    extracted = [
        extractor.extract(
            page=page,
            tenant_id=tenant_id,
            site_id=site_id,
            fetched_at=datetime.now(UTC),
            product=None,
        )
        for tenant_id, site_id in (
            ("tenant-a", "site-shared"),
            ("tenant-b", "site-shared"),
            ("tenant-a", "site-other"),
        )
    ]

    assert len({document.document_id for document in extracted}) == 3

    chunker = MarkdownChunker()
    chunk_ids = {
        document.document_id: {
            chunk.chunk_id
            for chunk in chunker.chunk(
                ParsedKnowledgeDocument(
                    path=Path(f"{document.document_id}.md"),
                    metadata={"document_id": document.document_id},
                    body=document.body,
                    internal_links=document.internal_links,
                    content_hash=document.content_hash,
                )
            )
        }
        for document in extracted
    }
    assert all(chunk_ids.values())
    assert chunk_ids[extracted[0].document_id].isdisjoint(chunk_ids[extracted[1].document_id])
    assert chunk_ids[extracted[0].document_id].isdisjoint(chunk_ids[extracted[2].document_id])


def test_web_quality_gate_requires_publishable_product_identity() -> None:
    gate = WebDocumentQualityGate()
    valid = gate.evaluate(structured_document())
    invalid = gate.evaluate(
        replace(
            structured_document(body="# Product\n\nToo short."),
            product={"sku": "SKU-100"},
        )
    )

    assert valid.accepted
    assert not invalid.accepted
    assert "body_too_short" in invalid.reason_codes
    assert "product_name_missing" in invalid.reason_codes


def test_content_freshness_prioritizes_delivery_when_no_volatile_fact_exists() -> None:
    parser = HtmlKnowledgeParser()
    page = parser.parse(
        requested_url="https://shop.example.com/faq.html",
        final_url="https://shop.example.com/faq.html",
        body=(
            b"<!doctype html><html><head><title>Delivery FAQ</title></head><body><main>"
            b"<h1>Delivery FAQ</h1><p>Delivery time is usually 7-20 business days.</p>"
            b"<p>Contact support if your parcel is delayed.</p></main></body></html>"
        ),
        content_type="text/html; charset=utf-8",
        allowed_hosts=frozenset({"shop.example.com"}),
    )
    document = StructuredContentExtractor().extract(
        page=page,
        tenant_id="tenant-a",
        site_id="site-a",
        fetched_at=datetime.now(UTC),
        product=None,
        response_bytes=2_000,
    )

    assert document.source_metadata["content_topics"] == ["delivery"]
    assert document.source_metadata["freshness_class"] == "operational"
    assert document.source_metadata["max_age_seconds"] == 7_200


@pytest.mark.parametrize(
    ("term", "topic", "freshness_class"),
    (
        ("Versandbedingungen und Lieferzeit", "delivery", "operational"),
        ("Rückgaberecht und Widerrufsrecht", "returns", "policy"),
        ("Datenschutzerklärung", "privacy", "policy"),
        ("Zahlungsarten und Bezahlung", "payment", "policy"),
        ("Garantie und Gewährleistung", "warranty", "policy"),
    ),
)
def test_content_freshness_maps_german_policy_terms_to_canonical_topics(
    term: str,
    topic: str,
    freshness_class: str,
) -> None:
    page = HtmlKnowledgeParser().parse(
        requested_url="https://shop.example.com/policy.html",
        final_url="https://shop.example.com/policy.html",
        body=(
            f"<html lang='de'><head><title>{term}</title></head><body><main>"
            f"<h1>{term}</h1><p>Diese Informationen gelten für alle Bestellungen. "
            "Bitte lesen Sie die Hinweise sorgfältig und kontaktieren Sie den Support "
            "bei Fragen.</p>"
            "</main></body></html>"
        ).encode(),
        content_type="text/html; charset=utf-8",
        allowed_hosts=frozenset({"shop.example.com"}),
    )

    document = StructuredContentExtractor().extract(
        page=page,
        tenant_id="tenant-a",
        site_id="site-a",
        fetched_at=datetime.now(UTC),
        product=None,
    )

    assert document.source_metadata["content_topics"] == [topic]
    assert document.source_metadata["freshness_class"] == freshness_class
    assert document.category == "policy"
    assert document.source_metadata["content_kind"] == "policy"


def test_content_freshness_preserves_multiple_german_policy_topics() -> None:
    page = HtmlKnowledgeParser().parse(
        requested_url="https://shop.example.com/legal.html",
        final_url="https://shop.example.com/legal.html",
        body=b"""<html lang='de'><head><title>Versand und Datenschutz</title></head><body><main>
        <h1>Versand und Datenschutz</h1><p>Versand, Zahlungsarten und Datenschutz
        sind in diesen Bedingungen beschrieben. Bitte wenden Sie sich bei Fragen an den Support.</p>
        </main></body></html>""",
        content_type="text/html; charset=utf-8",
        allowed_hosts=frozenset({"shop.example.com"}),
    )

    document = StructuredContentExtractor().extract(
        page=page,
        tenant_id="tenant-a",
        site_id="site-a",
        fetched_at=datetime.now(UTC),
        product=None,
    )

    assert document.source_metadata["content_topics"] == ["delivery", "privacy", "payment"]
    assert document.source_metadata["freshness_class"] == "operational"


def test_deduplicator_rejects_exact_and_near_duplicate_documents() -> None:
    deduplicator = WebDocumentDeduplicator(near_duplicate_threshold=0.9)
    original = structured_document()
    same_hash = replace(original, canonical_url="https://shop.example.com/copy.html")
    near_copy = replace(
        original,
        canonical_url="https://shop.example.com/near.html",
        content_hash="hash-b",
        body=original.body + " Additional.",
    )

    assert deduplicator.consider(original).accepted
    assert deduplicator.consider(same_hash).reason == "duplicate_content"
    assert deduplicator.consider(near_copy).reason == "near_duplicate_content"


async def test_crawler_discovers_sitemap_filters_pages_and_follows_internal_links() -> None:
    sitemap_url = "https://shop.example.com/sitemap.xml"
    product_url = "https://shop.example.com/products/example.html"
    duplicate_url = "https://shop.example.com/products/example-copy.html"
    responses = {
        sitemap_url: response(
            sitemap_url,
            (
                '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                f"<url><loc>{product_url}</loc></url>"
                f"<url><loc>{duplicate_url}</loc></url>"
                "<url><loc>https://shop.example.com/checkout/</loc></url>"
                "</urlset>"
            ).encode(),
            "application/xml",
        ),
        "https://shop.example.com/robots.txt": response(
            "https://shop.example.com/robots.txt",
            b"User-agent: *\nDisallow: /private/",
            "text/plain",
        ),
        product_url: response(product_url, PRODUCT_HTML, "text/html; charset=utf-8"),
        duplicate_url: response(duplicate_url, PRODUCT_HTML, "text/html; charset=utf-8"),
        "https://shop.example.com/faq.html": response(
            "https://shop.example.com/faq.html",
            FAQ_HTML,
            "text/html; charset=utf-8",
        ),
    }
    fetcher = FakeWebFetcher(responses)
    crawler = WebsiteKnowledgeCrawler(fetcher=fetcher)

    result = await crawler.crawl(
        WebCrawlPolicy(
            tenant_id="tenant-a",
            site_id="site-a",
            base_url="https://shop.example.com/",
            max_pages=10,
            crawl_delay_seconds=0,
        )
    )

    assert result.discovered_count == 4
    assert len(result.documents) == 2
    assert {item.title for item in result.documents} == {
        "Example Product",
        "Frequently Asked Questions",
    }
    assert result.excluded_count == 2
    assert result.failed_count == 0
    assert all(item.tenant_id == "tenant-a" for item in result.documents)
    assert all(item.site_id == "site-a" for item in result.documents)


async def test_crawler_reuses_robots_rules_across_single_page_crawls() -> None:
    page_url = "https://shop.example.com/products/example.html"
    robots_url = "https://shop.example.com/robots.txt"
    fetcher = FakeWebFetcher(
        {
            robots_url: response(
                robots_url,
                b"User-agent: *\nAllow: /",
                "text/plain",
            ),
            page_url: response(page_url, PRODUCT_HTML, "text/html; charset=utf-8"),
        }
    )
    crawler = WebsiteKnowledgeCrawler(fetcher=fetcher)
    policy = WebCrawlPolicy(
        tenant_id="tenant-a",
        site_id="site-a",
        base_url="https://shop.example.com/",
        seed_urls=(page_url,),
        max_pages=1,
        crawl_delay_seconds=0,
        discover_sitemaps=False,
        follow_internal_links=False,
    )

    await crawler.crawl(policy)
    await crawler.crawl(policy)

    assert [request.url for request in fetcher.requests].count(robots_url) == 1


async def test_crawler_excludes_language_paths_before_fetch_and_rejects_mismatched_language():
    translated_url = "https://shop.example.com/de/product.html"
    unexpected_language_url = "https://shop.example.com/product.html"
    german_html = PRODUCT_HTML.replace(b'<html lang="en-US">', b'<html lang="de">')
    fetcher = FakeWebFetcher(
        {
            "https://shop.example.com/robots.txt": response(
                "https://shop.example.com/robots.txt",
                b"User-agent: *\nAllow: /",
                "text/plain",
            ),
            unexpected_language_url: response(
                unexpected_language_url,
                german_html,
                "text/html; charset=utf-8",
            ),
        }
    )

    result = await WebsiteKnowledgeCrawler(fetcher=fetcher).crawl(
        WebCrawlPolicy(
            tenant_id="tenant-a",
            site_id="site-a",
            base_url="https://shop.example.com",
            seed_urls=(translated_url, unexpected_language_url),
            max_pages=2,
            crawl_delay_seconds=0,
            discover_sitemaps=False,
            follow_internal_links=False,
            language="en",
            translated_locales=("de",),
            enforce_primary_language=True,
        )
    )

    assert translated_url not in {request.url for request in fetcher.requests}
    assert result.documents == ()
    assert result.failed_count == 0
    assert result.excluded_count == 2
    assert result.errors[translated_url] == "excluded: translated_language_path"
    assert result.errors[unexpected_language_url] == "excluded: page_language_mismatch"


async def test_crawler_excludes_unsupported_content_type_without_failing_snapshot() -> None:
    binary_url = "https://shop.example.com/protocol-endpoint"
    fetcher = FakeWebFetcher(
        {binary_url: UnsupportedWebContentTypeError("application/octet-stream")}
    )
    crawler = WebsiteKnowledgeCrawler(fetcher=fetcher)

    result = await crawler.crawl(
        WebCrawlPolicy(
            tenant_id="tenant-a",
            site_id="site-a",
            base_url="https://shop.example.com/",
            seed_urls=(binary_url,),
            max_pages=1,
            crawl_delay_seconds=0,
            follow_internal_links=False,
            respect_robots_txt=False,
            discover_sitemaps=False,
        )
    )

    assert result.documents == ()
    assert result.excluded_count == 1
    assert result.failed_count == 0
    assert result.errors[binary_url] == (
        "excluded: unsupported_content_type:application/octet-stream"
    )


async def test_crawler_propagates_retry_after_for_transient_http_status() -> None:
    url = "https://shop.example.com/busy"
    fetcher = FakeWebFetcher(
        {
            url: WebFetchResponse(
                requested_url=url,
                final_url=url,
                status_code=503,
                content_type="text/plain",
                body=b"",
                headers={"retry-after": "45"},
            )
        }
    )

    with pytest.raises(RetryableWebFetchError) as raised:
        await WebsiteKnowledgeCrawler(fetcher=fetcher).crawl(
            WebCrawlPolicy(
                tenant_id="tenant-a",
                site_id="site-a",
                base_url="https://shop.example.com/",
                seed_urls=(url,),
                max_pages=1,
                crawl_delay_seconds=0,
                follow_internal_links=False,
                respect_robots_txt=False,
                discover_sitemaps=False,
            )
        )

    assert raised.value.status_code == 503
    assert raised.value.retry_after_seconds == 45


async def test_crawler_classifies_gone_and_noindex_pages_as_exclusions() -> None:
    gone_url = "https://shop.example.com/removed"
    noindex_url = "https://shop.example.com/private-guide"
    fetcher = FakeWebFetcher(
        {
            gone_url: WebFetchResponse(
                requested_url=gone_url,
                final_url=gone_url,
                status_code=410,
                content_type="text/plain",
                body=b"",
            ),
            noindex_url: response(
                noindex_url,
                b"<html><head><meta name='robots' content='noindex, follow'></head>"
                b"<body><main><h1>Private guide</h1></main></body></html>",
                "text/html",
            ),
        }
    )

    result = await WebsiteKnowledgeCrawler(fetcher=fetcher).crawl(
        WebCrawlPolicy(
            tenant_id="tenant-a",
            site_id="site-a",
            base_url="https://shop.example.com/",
            seed_urls=(gone_url, noindex_url),
            max_pages=2,
            crawl_delay_seconds=0,
            follow_internal_links=False,
            respect_robots_txt=False,
            discover_sitemaps=False,
        )
    )

    assert result.failed_count == 0
    assert result.excluded_count == 2
    assert result.errors[gone_url] == "excluded: gone:http_410"
    assert result.errors[noindex_url] == "excluded: noindex"


async def test_crawler_uses_active_validators_and_reports_not_modified_pages() -> None:
    sitemap_url = "https://shop.example.com/sitemap.xml"
    product_url = "https://shop.example.com/products/example.html"
    responses = {
        sitemap_url: response(
            sitemap_url,
            (
                '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                f"<url><loc>{product_url}</loc></url></urlset>"
            ).encode(),
            "application/xml",
        ),
        "https://shop.example.com/robots.txt": response(
            "https://shop.example.com/robots.txt",
            b"User-agent: *\nAllow: /",
            "text/plain",
        ),
        product_url: WebFetchResponse(
            requested_url=product_url,
            final_url=product_url,
            status_code=304,
            content_type="application/octet-stream",
            body=b"",
        ),
    }
    fetcher = FakeWebFetcher(responses)
    crawler = WebsiteKnowledgeCrawler(fetcher=fetcher)

    result = await crawler.crawl(
        WebCrawlPolicy(
            tenant_id="tenant-a",
            site_id="site-a",
            base_url="https://shop.example.com/",
            crawl_delay_seconds=0,
            validators=(
                WebCrawlValidator(
                    document_id="document-a",
                    version_id="version-a",
                    canonical_url=product_url,
                    requested_url=product_url,
                    final_url=product_url,
                    etag='"product-v1"',
                    last_modified="Sun, 27 Jul 2026 00:00:00 GMT",
                    product_key="SKU-100",
                ),
            ),
        )
    )

    product_request = next(request for request in fetcher.requests if request.url == product_url)
    assert product_request.if_none_match == '"product-v1"'
    assert product_request.if_modified_since == "Sun, 27 Jul 2026 00:00:00 GMT"
    assert result.documents == ()
    assert result.failed_count == 0
    assert result.unchanged_documents[0].version_id == "version-a"


async def test_crawler_prioritizes_explicit_seed_url_before_sitemap_pages() -> None:
    seed_input_url = "https://shop.example.com/agent-test.html?121"
    seed_url = canonicalize_url(seed_input_url)
    sitemap_url = "https://shop.example.com/sitemap.xml"
    responses = {
        sitemap_url: response(
            sitemap_url,
            b"<?xml version='1.0'?><urlset><url><loc>https://shop.example.com/faq.html</loc></url></urlset>",
            "application/xml",
        ),
        "https://shop.example.com/robots.txt": response(
            "https://shop.example.com/robots.txt",
            b"User-agent: *\nAllow: /",
            "text/plain",
        ),
        seed_url: response(seed_url, PRODUCT_HTML, "text/html; charset=utf-8"),
    }
    crawler = WebsiteKnowledgeCrawler(fetcher=FakeWebFetcher(responses))

    result = await crawler.crawl(
        WebCrawlPolicy(
            tenant_id="tenant-a",
            site_id="site-a",
            base_url="https://shop.example.com/",
            seed_urls=(seed_input_url,),
            max_pages=1,
            crawl_delay_seconds=0,
        )
    )

    assert len(result.documents) == 1
    assert result.documents[0].source_metadata["requested_url"] == seed_url
    assert result.documents[0].canonical_url == "https://shop.example.com/products/example.html"


async def test_crawler_marks_sitemap_crawl_truncated_at_page_limit() -> None:
    sitemap_url = "https://shop.example.com/sitemap.xml"
    first_url = "https://shop.example.com/products/first.html"
    second_url = "https://shop.example.com/products/second.html"
    responses = {
        sitemap_url: response(
            sitemap_url,
            (
                "<?xml version='1.0'?><urlset>"
                f"<url><loc>{first_url}</loc></url>"
                f"<url><loc>{second_url}</loc></url>"
                "</urlset>"
            ).encode(),
            "application/xml",
        ),
        "https://shop.example.com/robots.txt": response(
            "https://shop.example.com/robots.txt",
            b"User-agent: *\nAllow: /",
            "text/plain",
        ),
        first_url: response(first_url, PRODUCT_HTML, "text/html; charset=utf-8"),
    }
    crawler = WebsiteKnowledgeCrawler(fetcher=FakeWebFetcher(responses))

    result = await crawler.crawl(
        WebCrawlPolicy(
            tenant_id="tenant-a",
            site_id="site-a",
            base_url="https://shop.example.com/",
            max_pages=1,
            crawl_delay_seconds=0,
        )
    )

    assert result.truncated is True
    assert result.discovered_count >= 2
    assert len(result.documents) == 1


async def test_crawler_marks_nested_sitemap_discovery_truncated_at_limit() -> None:
    sitemap_url = "https://shop.example.com/sitemap.xml"
    first_nested = "https://shop.example.com/products-1.xml"
    second_nested = "https://shop.example.com/products-2.xml"
    product_url = "https://shop.example.com/products/example.html"
    responses = {
        sitemap_url: response(
            sitemap_url,
            (
                "<?xml version='1.0'?><sitemapindex>"
                f"<sitemap><loc>{first_nested}</loc></sitemap>"
                f"<sitemap><loc>{second_nested}</loc></sitemap>"
                "</sitemapindex>"
            ).encode(),
            "application/xml",
        ),
        first_nested: response(
            first_nested,
            (f"<?xml version='1.0'?><urlset><url><loc>{product_url}</loc></url></urlset>").encode(),
            "application/xml",
        ),
        "https://shop.example.com/robots.txt": response(
            "https://shop.example.com/robots.txt",
            b"User-agent: *\nAllow: /",
            "text/plain",
        ),
        product_url: response(product_url, PRODUCT_HTML, "text/html; charset=utf-8"),
        "https://shop.example.com/faq.html": response(
            "https://shop.example.com/faq.html",
            FAQ_HTML,
            "text/html; charset=utf-8",
        ),
    }
    crawler = WebsiteKnowledgeCrawler(fetcher=FakeWebFetcher(responses))

    result = await crawler.crawl(
        WebCrawlPolicy(
            tenant_id="tenant-a",
            site_id="site-a",
            base_url="https://shop.example.com/",
            max_sitemaps=2,
            max_pages=10,
            crawl_delay_seconds=0,
        )
    )

    assert result.truncated is True
    assert result.failed_count == 0
    assert len(result.documents) == 2


async def test_seed_only_batch_uses_validators_without_reporting_truncation() -> None:
    product_url = "https://shop.example.com/products/example.html"
    fetcher = FakeWebFetcher(
        {
            "https://shop.example.com/robots.txt": response(
                "https://shop.example.com/robots.txt",
                b"User-agent: *\nAllow: /",
                "text/plain",
            ),
            product_url: WebFetchResponse(
                requested_url=product_url,
                final_url=product_url,
                status_code=304,
                content_type="application/octet-stream",
                body=b"",
            ),
        }
    )
    crawler = WebsiteKnowledgeCrawler(fetcher=fetcher)

    result = await crawler.crawl(
        WebCrawlPolicy(
            tenant_id="tenant-a",
            site_id="site-a",
            base_url="https://shop.example.com/",
            seed_urls=(product_url,),
            max_pages=1,
            crawl_delay_seconds=0,
            discover_sitemaps=False,
            follow_internal_links=False,
            validators=(
                WebCrawlValidator(
                    document_id="document-a",
                    version_id="version-a",
                    canonical_url=product_url,
                    requested_url=product_url,
                    final_url=product_url,
                    etag='"product-v1"',
                    last_modified="Sun, 27 Jul 2026 00:00:00 GMT",
                    product_key="SKU-100",
                ),
            ),
        )
    )

    product_request = next(request for request in fetcher.requests if request.url == product_url)
    assert product_request.if_none_match == '"product-v1"'
    assert result.truncated is False
    assert result.failed_count == 0
    assert len(result.unchanged_documents) == 1
    assert result.unchanged_documents[0].product_key == "SKU-100"


class StubCrawler:
    def __init__(self, document: StructuredWebDocument) -> None:
        self.document = document
        self.failed_count = 0
        self.not_modified = False
        self.truncated = False

    async def crawl(self, policy: WebCrawlPolicy) -> WebCrawlResult:
        if self.not_modified:
            validator = policy.validators[0]
            return WebCrawlResult(
                discovered_count=1,
                documents=(),
                excluded_count=0,
                failed_count=0,
                errors={},
                unchanged_documents=(
                    UnchangedWebDocument(
                        document_id=validator.document_id,
                        version_id=validator.version_id,
                        canonical_url=validator.canonical_url,
                        checked_at=datetime.now(UTC),
                        etag=validator.etag,
                        last_modified=validator.last_modified,
                    ),
                ),
            )
        return WebCrawlResult(
            1,
            (self.document,) if not self.failed_count else (),
            0,
            self.failed_count,
            {"page": "fetch_failed"} if self.failed_count else {},
            truncated=self.truncated,
        )


class DuplicateProductCrawler:
    async def crawl(self, policy: WebCrawlPolicy) -> WebCrawlResult:
        first = structured_document()
        second = replace(
            first,
            document_id="document-b",
            canonical_url="https://shop.example.com/product-alias.html",
            content_hash="hash-b",
            source_metadata={
                **first.source_metadata,
                "requested_url": "https://shop.example.com/product-alias.html",
                "final_url": "https://shop.example.com/product-alias.html",
            },
        )
        return WebCrawlResult(
            discovered_count=2,
            documents=(first, second),
            excluded_count=0,
            failed_count=0,
            errors={},
        )


class FailingPageIndexer(InMemoryKnowledgeIndexer):
    async def upsert(self, chunks):  # type: ignore[no-untyped-def]
        raise RuntimeError("qdrant staging failed")


class CleanupFailingPageIndexer(FailingPageIndexer):
    async def discard_staged_document(self, **values):  # type: ignore[no-untyped-def]
        del values
        raise RuntimeError("qdrant cleanup failed")


async def test_failed_staged_page_discards_only_its_artifacts() -> None:
    crawler = StubCrawler(structured_document())
    control_plane = InMemoryKnowledgeControlPlane()
    indexer = FailingPageIndexer()
    catalog = InMemoryProductCatalog()
    service = WebKnowledgeSyncService(
        crawler=crawler,  # type: ignore[arg-type]
        chunker=MarkdownChunker(max_characters=200),
        embedding_provider=DeterministicEmbeddingProvider(),
        sparse_embedding_provider=DeterministicSparseEmbeddingProvider(),
        indexer=indexer,
        control_plane=control_plane,
        product_catalog=catalog,
    )
    policy = WebCrawlPolicy(
        tenant_id="tenant-a",
        site_id="site-a",
        base_url="https://shop.example.com/",
        crawl_delay_seconds=0,
    )

    await service.begin_staged_sync(policy, snapshot_id="failed-page")
    with pytest.raises(RuntimeError, match="qdrant staging failed"):
        await service.stage_page(policy, snapshot_id="failed-page")

    assert not catalog.products
    assert not indexer.chunks
    assert all(
        snapshot.index_status == "discarded"
        for snapshot in control_plane.version_snapshots.values()
    )


async def test_failed_page_requires_remediation_when_staging_cleanup_fails() -> None:
    service = WebKnowledgeSyncService(
        crawler=StubCrawler(structured_document()),  # type: ignore[arg-type]
        chunker=MarkdownChunker(max_characters=200),
        embedding_provider=DeterministicEmbeddingProvider(),
        sparse_embedding_provider=DeterministicSparseEmbeddingProvider(),
        indexer=CleanupFailingPageIndexer(),
        control_plane=InMemoryKnowledgeControlPlane(),
        product_catalog=InMemoryProductCatalog(),
    )
    policy = WebCrawlPolicy(
        tenant_id="tenant-a",
        site_id="site-a",
        base_url="https://shop.example.com/",
        crawl_delay_seconds=0,
    )

    await service.begin_staged_sync(policy, snapshot_id="cleanup-failure")
    with pytest.raises(StagingCleanupRequiredError, match="staging_cleanup_required"):
        await service.stage_page(policy, snapshot_id="cleanup-failure")


async def test_web_sync_is_incremental_and_replaces_changed_document_projection() -> None:
    crawler = StubCrawler(structured_document())
    control_plane = InMemoryKnowledgeControlPlane()
    indexer = InMemoryKnowledgeIndexer()
    service = WebKnowledgeSyncService(
        crawler=crawler,  # type: ignore[arg-type]
        chunker=MarkdownChunker(max_characters=200),
        embedding_provider=DeterministicEmbeddingProvider(),
        sparse_embedding_provider=DeterministicSparseEmbeddingProvider(),
        indexer=indexer,
        control_plane=control_plane,
        product_catalog=InMemoryProductCatalog(),
        index_namespace="web-test-v1",
    )
    policy = WebCrawlPolicy(
        tenant_id="tenant-a",
        site_id="site-a",
        base_url="https://shop.example.com/",
        crawl_delay_seconds=0,
    )

    first = await service.sync(policy)
    second = await service.sync(policy)
    crawler.document = replace(
        crawler.document,
        body=crawler.document.body + " Changed content for a new version.",
        content_hash="hash-changed",
    )
    third = await service.sync(policy)

    assert first.indexed_count > 0
    assert second.skipped_count == 1
    assert second.indexed_count == 0
    assert third.indexed_count > 0
    assert indexer.deleted_documents == []
    assert first.published is True
    assert first.product_count == 1
    assert second.published is True
    assert third.published is True
    assert all(chunk.metadata["knowledge_scope"] == "site" for chunk in indexer.chunks.values())
    assert all(chunk.metadata["site_id"] == "site-a" for chunk in indexer.chunks.values())
    assert all(
        chunk.metadata["canonical_path"] == "/product.html" for chunk in indexer.chunks.values()
    )
    assert control_plane.staged[-1].source_path == "https://shop.example.com/product.html"


async def test_web_sync_reports_duplicate_product_identity() -> None:
    control_plane = InMemoryKnowledgeControlPlane()
    indexer = InMemoryKnowledgeIndexer()
    service = WebKnowledgeSyncService(
        crawler=DuplicateProductCrawler(),  # type: ignore[arg-type]
        chunker=MarkdownChunker(max_characters=200),
        embedding_provider=DeterministicEmbeddingProvider(),
        sparse_embedding_provider=DeterministicSparseEmbeddingProvider(),
        indexer=indexer,
        control_plane=control_plane,
        product_catalog=InMemoryProductCatalog(),
    )

    with pytest.raises(ProductIdentityConflictError, match="product_identity_conflict:SKU-100"):
        await service.sync(
            WebCrawlPolicy(
                tenant_id="tenant-a",
                site_id="site-a",
                base_url="https://shop.example.com/",
                crawl_delay_seconds=0,
            )
        )

    assert len(control_plane.conflicts) == 1
    assert control_plane.conflicts[0][1].startswith("product_identity:site-a:SKU-100")
    assert not any(chunk.metadata.get("is_active") for chunk in indexer.chunks.values())


async def test_web_sync_updates_price_snapshot_without_reembedding_unchanged_text() -> None:
    document = replace(
        structured_document(),
        product={
            "name": "Product",
            "sku": "SKU-100",
            "offers": {"price": "579", "priceCurrency": "USD"},
        },
    )
    crawler = StubCrawler(document)
    control_plane = InMemoryKnowledgeControlPlane()
    indexer = InMemoryKnowledgeIndexer()
    catalog = InMemoryProductCatalog()
    service = WebKnowledgeSyncService(
        crawler=crawler,  # type: ignore[arg-type]
        chunker=MarkdownChunker(max_characters=200),
        embedding_provider=DeterministicEmbeddingProvider(),
        sparse_embedding_provider=DeterministicSparseEmbeddingProvider(),
        indexer=indexer,
        control_plane=control_plane,
        product_catalog=catalog,
        index_namespace="web-test-v1",
    )
    policy = WebCrawlPolicy(
        tenant_id="tenant-a",
        site_id="site-a",
        base_url="https://shop.example.com/",
        crawl_delay_seconds=0,
    )

    first = await service.sync(policy)
    crawler.document = replace(
        document,
        product={
            "name": "Product",
            "sku": "SKU-100",
            "offers": {"price": "599", "priceCurrency": "USD"},
        },
        content_hash="hash-price-599",
    )
    second = await service.sync(policy)

    active_snapshot_id = catalog.active_snapshots[("tenant-a", "site-a")]
    active_product = catalog.products[("tenant-a", "site-a", active_snapshot_id, "SKU-100")]
    assert first.indexed_count > 0
    assert second.skipped_count == 1
    assert second.indexed_count == 0
    assert second.published is True
    assert active_snapshot_id == second.sync_job_id
    assert active_product.price == "599"
    assert active_product.content_hash == "hash-price-599"


async def test_web_sync_reuses_304_product_in_new_complete_snapshot() -> None:
    document = replace(
        structured_document(),
        product={
            "name": "Product",
            "sku": "SKU-100",
            "offers": {"price": "579", "priceCurrency": "USD"},
        },
        source_metadata={
            "source_type": "website_html",
            "etag": '"product-v1"',
            "last_modified": "Sun, 27 Jul 2026 00:00:00 GMT",
        },
    )
    crawler = StubCrawler(document)
    control_plane = InMemoryKnowledgeControlPlane()
    indexer = InMemoryKnowledgeIndexer()
    catalog = InMemoryProductCatalog()
    service = WebKnowledgeSyncService(
        crawler=crawler,  # type: ignore[arg-type]
        chunker=MarkdownChunker(max_characters=200),
        embedding_provider=DeterministicEmbeddingProvider(),
        sparse_embedding_provider=DeterministicSparseEmbeddingProvider(),
        indexer=indexer,
        control_plane=control_plane,
        product_catalog=catalog,
        index_namespace="web-test-v1",
    )
    policy = WebCrawlPolicy(
        tenant_id="tenant-a",
        site_id="site-a",
        base_url="https://shop.example.com/",
        crawl_delay_seconds=0,
    )

    first = await service.sync(policy)
    crawler.not_modified = True
    second = await service.sync(policy)

    active_snapshot_id = catalog.active_snapshots[("tenant-a", "site-a")]
    active_product = catalog.products[("tenant-a", "site-a", active_snapshot_id, "SKU-100")]
    assert first.indexed_count > 0
    assert second.indexed_count == 0
    assert second.skipped_count == 1
    assert second.document_count == 1
    assert second.http_not_modified_count == 1
    assert second.published is True
    assert active_snapshot_id == second.sync_job_id
    assert active_product.price == "579"
    assert active_product.status.value == "valid"
    assert active_product.missing_count == 0


async def test_staged_web_sync_reconciles_and_publishes_complete_snapshot() -> None:
    crawler = StubCrawler(structured_document())
    control_plane = InMemoryKnowledgeControlPlane()
    indexer = InMemoryKnowledgeIndexer()
    catalog = InMemoryProductCatalog()
    service = WebKnowledgeSyncService(
        crawler=crawler,  # type: ignore[arg-type]
        chunker=MarkdownChunker(max_characters=200),
        embedding_provider=DeterministicEmbeddingProvider(),
        sparse_embedding_provider=DeterministicSparseEmbeddingProvider(),
        indexer=indexer,
        control_plane=control_plane,
        product_catalog=catalog,
        index_namespace="web-test-v1",
    )
    policy = WebCrawlPolicy(
        tenant_id="tenant-a",
        site_id="site-a",
        base_url="https://shop.example.com/",
        crawl_delay_seconds=0,
    )

    await service.begin_staged_sync(policy, snapshot_id="production-1")
    report = await service.stage_page(policy, snapshot_id="production-1")
    activation = await service.publish_staged_sync(
        policy,
        snapshot_id="production-1",
        active_version_ids=(report.validators[0].version_id,),
        staged_version_ids=(report.validators[0].version_id,),
        expected_chunk_count=report.indexed_count,
        expected_product_count=report.product_count,
        discovered_count=report.discovered_count,
        errors=report.errors,
    )

    assert activation.activated_count == 1
    assert catalog.active_snapshots[("tenant-a", "site-a")] == "production-1"
    assert (
        await indexer.count_snapshot_points(
            tenant_id="tenant-a",
            site_id="site-a",
            snapshot_id="production-1",
        )
        == report.indexed_count
    )
    assert all(chunk.metadata["is_active"] is True for chunk in indexer.chunks.values())
    publication = await control_plane.get_site_publication_state(
        tenant_id="tenant-a",
        site_id="site-a",
    )
    assert publication.state == "active"
    assert publication.active_publication_id == "production-1"
    assert publication.pending_publication_id is None


async def test_staged_web_sync_refuses_reconciliation_mismatch() -> None:
    control_plane = InMemoryKnowledgeControlPlane()
    indexer = InMemoryKnowledgeIndexer()
    catalog = InMemoryProductCatalog()
    service = WebKnowledgeSyncService(
        crawler=StubCrawler(structured_document()),  # type: ignore[arg-type]
        chunker=MarkdownChunker(max_characters=200),
        embedding_provider=DeterministicEmbeddingProvider(),
        sparse_embedding_provider=DeterministicSparseEmbeddingProvider(),
        indexer=indexer,
        control_plane=control_plane,
        product_catalog=catalog,
    )
    policy = WebCrawlPolicy(
        tenant_id="tenant-a",
        site_id="site-a",
        base_url="https://shop.example.com/",
        crawl_delay_seconds=0,
    )

    await service.begin_staged_sync(policy, snapshot_id="production-mismatch")
    report = await service.stage_page(policy, snapshot_id="production-mismatch")

    with pytest.raises(RuntimeError, match="reconciliation failed"):
        await service.publish_staged_sync(
            policy,
            snapshot_id="production-mismatch",
            active_version_ids=(report.validators[0].version_id,),
            staged_version_ids=(report.validators[0].version_id,),
            expected_chunk_count=report.indexed_count + 1,
            expected_product_count=report.product_count,
            discovered_count=report.discovered_count,
            errors=report.errors,
        )

    assert ("tenant-a", "site-a") not in catalog.active_snapshots
    assert not any(chunk.metadata.get("is_active") for chunk in indexer.chunks.values())


class FailingActivationProductCatalog(InMemoryProductCatalog):
    def __init__(self) -> None:
        super().__init__()
        self.fail_activation = False

    async def activate_snapshot(self, **values):  # type: ignore[no-untyped-def]
        if self.fail_activation:
            raise RuntimeError("product activation failed")
        return await super().activate_snapshot(**values)


async def test_staged_web_sync_rolls_back_knowledge_when_product_activation_fails() -> None:
    crawler = StubCrawler(structured_document())
    control_plane = InMemoryKnowledgeControlPlane()
    indexer = InMemoryKnowledgeIndexer()
    catalog = FailingActivationProductCatalog()
    service = WebKnowledgeSyncService(
        crawler=crawler,  # type: ignore[arg-type]
        chunker=MarkdownChunker(max_characters=200),
        embedding_provider=DeterministicEmbeddingProvider(),
        sparse_embedding_provider=DeterministicSparseEmbeddingProvider(),
        indexer=indexer,
        control_plane=control_plane,
        product_catalog=catalog,
        index_namespace="web-test-v1",
    )
    policy = WebCrawlPolicy(
        tenant_id="tenant-a",
        site_id="site-a",
        base_url="https://shop.example.com/",
        crawl_delay_seconds=0,
    )
    baseline = await service.sync(policy)
    previous_versions = await control_plane.list_active_site_version_ids(
        tenant_id="tenant-a",
        site_id="site-a",
    )
    previous_product_snapshot = catalog.active_snapshots[("tenant-a", "site-a")]
    crawler.document = replace(
        crawler.document,
        body=crawler.document.body + " New production content.",
        content_hash="changed-production-content",
    )
    await service.begin_staged_sync(policy, snapshot_id="production-rollback")
    report = await service.stage_page(policy, snapshot_id="production-rollback")
    catalog.fail_activation = True

    with pytest.raises(RuntimeError, match="product activation failed"):
        await service.publish_staged_sync(
            policy,
            snapshot_id="production-rollback",
            active_version_ids=(report.validators[0].version_id,),
            staged_version_ids=(report.validators[0].version_id,),
            expected_chunk_count=report.indexed_count,
            expected_product_count=report.product_count,
            discovered_count=report.discovered_count,
            errors=report.errors,
        )

    assert baseline.published is True
    assert (
        await control_plane.list_active_site_version_ids(
            tenant_id="tenant-a",
            site_id="site-a",
        )
        == previous_versions
    )
    assert catalog.active_snapshots[("tenant-a", "site-a")] == previous_product_snapshot
    publication = await control_plane.get_site_publication_state(
        tenant_id="tenant-a",
        site_id="site-a",
    )
    assert publication.state == "active"
    assert publication.active_publication_id == baseline.sync_job_id
    assert publication.pending_publication_id is None
    assert publication.error_code == "RuntimeError"


class FailingRollbackKnowledgeIndexer(InMemoryKnowledgeIndexer):
    def __init__(self) -> None:
        super().__init__()
        self.rollback_versions: tuple[str, ...] = ()

    async def activate_site_versions(self, **values):  # type: ignore[no-untyped-def]
        if tuple(values["version_ids"]) == self.rollback_versions:
            raise RuntimeError("Qdrant rollback failed")
        await super().activate_site_versions(**values)


async def test_staged_web_sync_requires_recovery_when_rollback_is_incomplete() -> None:
    crawler = StubCrawler(structured_document())
    control_plane = InMemoryKnowledgeControlPlane()
    indexer = FailingRollbackKnowledgeIndexer()
    catalog = FailingActivationProductCatalog()
    service = WebKnowledgeSyncService(
        crawler=crawler,  # type: ignore[arg-type]
        chunker=MarkdownChunker(max_characters=200),
        embedding_provider=DeterministicEmbeddingProvider(),
        sparse_embedding_provider=DeterministicSparseEmbeddingProvider(),
        indexer=indexer,
        control_plane=control_plane,
        product_catalog=catalog,
        index_namespace="web-test-v1",
    )
    policy = WebCrawlPolicy(
        tenant_id="tenant-a",
        site_id="site-a",
        base_url="https://shop.example.com/",
        crawl_delay_seconds=0,
    )
    baseline = await service.sync(policy)
    previous_versions = await control_plane.list_active_site_version_ids(
        tenant_id="tenant-a",
        site_id="site-a",
    )
    crawler.document = replace(
        crawler.document,
        body=crawler.document.body + " New production content.",
        content_hash="changed-recovery-content",
    )
    await service.begin_staged_sync(policy, snapshot_id="production-recovery")
    report = await service.stage_page(policy, snapshot_id="production-recovery")
    indexer.rollback_versions = previous_versions
    catalog.fail_activation = True

    with pytest.raises(RuntimeError, match="product activation failed"):
        await service.publish_staged_sync(
            policy,
            snapshot_id="production-recovery",
            active_version_ids=(report.validators[0].version_id,),
            staged_version_ids=(report.validators[0].version_id,),
            expected_chunk_count=report.indexed_count,
            expected_product_count=report.product_count,
            discovered_count=report.discovered_count,
            errors=report.errors,
        )

    publication = await control_plane.get_site_publication_state(
        tenant_id="tenant-a",
        site_id="site-a",
    )
    assert publication.state == "recovery_required"
    assert publication.active_publication_id == baseline.sync_job_id
    assert publication.pending_publication_id == "production-recovery"
    assert publication.error_code == "rollback_failed:RuntimeError"


async def test_web_sync_quarantines_failed_quality_document_with_audit_record() -> None:
    document = replace(
        structured_document(body="# Product\n\nToo short."),
        product={"sku": "SKU-100"},
    )
    control_plane = InMemoryKnowledgeControlPlane()
    indexer = InMemoryKnowledgeIndexer()
    service = WebKnowledgeSyncService(
        crawler=StubCrawler(document),  # type: ignore[arg-type]
        chunker=MarkdownChunker(max_characters=200),
        embedding_provider=DeterministicEmbeddingProvider(),
        sparse_embedding_provider=DeterministicSparseEmbeddingProvider(),
        indexer=indexer,
        control_plane=control_plane,
        product_catalog=InMemoryProductCatalog(),
    )

    report = await service.sync(
        WebCrawlPolicy(
            tenant_id="tenant-a",
            site_id="site-a",
            base_url="https://shop.example.com/",
            crawl_delay_seconds=0,
        )
    )

    assert report.indexed_count == 0
    assert report.excluded_count == 1
    assert report.failed_count == 0
    assert not indexer.chunks
    assert len(control_plane.ingestion_rejections) == 1
    rejection = control_plane.ingestion_rejections[0]
    assert rejection[0] == "tenant-a"
    assert rejection[2] == document.canonical_url
    assert rejection[3] == "web_quality:body_too_short,product_name_missing"


async def test_failed_web_sync_keeps_previous_complete_snapshot_active() -> None:
    crawler = StubCrawler(structured_document())
    control_plane = InMemoryKnowledgeControlPlane()
    indexer = InMemoryKnowledgeIndexer()
    catalog = InMemoryProductCatalog()
    service = WebKnowledgeSyncService(
        crawler=crawler,  # type: ignore[arg-type]
        chunker=MarkdownChunker(max_characters=200),
        embedding_provider=DeterministicEmbeddingProvider(),
        sparse_embedding_provider=DeterministicSparseEmbeddingProvider(),
        indexer=indexer,
        control_plane=control_plane,
        product_catalog=catalog,
    )
    policy = WebCrawlPolicy(
        tenant_id="tenant-a",
        site_id="site-a",
        base_url="https://shop.example.com/",
        crawl_delay_seconds=0,
    )

    first = await service.sync(policy)
    active_before = {
        key for key, chunk in indexer.chunks.items() if chunk.metadata.get("is_active") is True
    }
    active_snapshot_before = catalog.active_snapshots[("tenant-a", "site-a")]
    crawler.failed_count = 1
    second = await service.sync(policy)

    assert first.published is True
    assert second.published is False
    assert {
        key for key, chunk in indexer.chunks.items() if chunk.metadata.get("is_active") is True
    } == active_before
    assert catalog.active_snapshots[("tenant-a", "site-a")] == active_snapshot_before
    assert catalog.failed_snapshots


async def test_truncated_web_sync_keeps_previous_complete_snapshot_active() -> None:
    crawler = StubCrawler(structured_document())
    control_plane = InMemoryKnowledgeControlPlane()
    indexer = InMemoryKnowledgeIndexer()
    catalog = InMemoryProductCatalog()
    service = WebKnowledgeSyncService(
        crawler=crawler,  # type: ignore[arg-type]
        chunker=MarkdownChunker(max_characters=200),
        embedding_provider=DeterministicEmbeddingProvider(),
        sparse_embedding_provider=DeterministicSparseEmbeddingProvider(),
        indexer=indexer,
        control_plane=control_plane,
        product_catalog=catalog,
    )
    policy = WebCrawlPolicy(
        tenant_id="tenant-a",
        site_id="site-a",
        base_url="https://shop.example.com/",
        max_pages=1,
        crawl_delay_seconds=0,
    )

    first = await service.sync(policy)
    active_snapshot_before = catalog.active_snapshots[("tenant-a", "site-a")]
    crawler.truncated = True
    second = await service.sync(policy)

    assert first.published is True
    assert second.published is False
    assert second.failed_count == 1
    assert second.errors == {
        "crawl_limit": "crawl stopped at max_pages=1; incomplete snapshot refused"
    }
    assert catalog.active_snapshots[("tenant-a", "site-a")] == active_snapshot_before
    assert catalog.failed_snapshots[-1][2] == second.sync_job_id
