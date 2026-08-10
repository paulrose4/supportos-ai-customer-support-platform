import asyncio
import gzip
import ipaddress
import socket
from http.client import HTTPConnection, HTTPSConnection
from io import BytesIO
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import (
    HTTPHandler,
    HTTPRedirectHandler,
    HTTPSHandler,
    OpenerDirector,
    ProxyHandler,
    Request,
    build_opener,
)

from app.domain.ports.web_knowledge import (
    ResponseBudgetExceededError,
    UnsupportedWebContentTypeError,
    WebFetchRequest,
    WebFetchResponse,
    WebTransportError,
)
from app.knowledge.web.canonicalizer import canonicalize_url, origin_for_url
from app.knowledge.web.language_scope import is_translated_url


class SafeHttpFetcher:
    async def fetch(self, request: WebFetchRequest) -> WebFetchResponse:
        return await asyncio.to_thread(self._fetch_sync, request)

    def _fetch_sync(self, request: WebFetchRequest) -> WebFetchResponse:
        normalized_url = _validate_public_url(
            request.url,
            request.allowed_hosts,
            request.allowed_origins,
            request.preserve_query,
        )
        if is_translated_url(normalized_url, tuple(request.blocked_first_path_segments)):
            raise PermissionError("web URL uses a blocked language path")
        opener = (
            _build_opener(
                request.allowed_hosts,
                allowed_origins=request.allowed_origins,
                preserve_query=request.preserve_query,
                blocked_first_path_segments=request.blocked_first_path_segments,
            )
            if request.blocked_first_path_segments
            else _build_opener(
                request.allowed_hosts,
                allowed_origins=request.allowed_origins,
                preserve_query=request.preserve_query,
            )
        )
        http_request = Request(
            normalized_url,
            headers={
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml,"
                    "text/xml,text/plain;q=0.8,*/*;q=0.1"
                ),
                "Accept-Encoding": "gzip",
                "User-Agent": request.user_agent,
                **(
                    {"If-None-Match": request.if_none_match}
                    if request.if_none_match is not None
                    else {}
                ),
                **(
                    {"If-Modified-Since": request.if_modified_since}
                    if request.if_modified_since is not None
                    else {}
                ),
            },
            method="GET",
        )
        try:
            response = opener.open(http_request, timeout=request.timeout_seconds)
        except HTTPError as error:
            response = error
        except PermissionError:
            raise
        except URLError as error:
            if isinstance(error.reason, PermissionError):
                raise error.reason from error
            raise WebTransportError(f"web fetch failed: {type(error).__name__}") from error
        except (OSError, TimeoutError) as error:
            raise WebTransportError(f"web fetch failed: {type(error).__name__}") from error
        with response:
            final_url = _validate_public_url(
                response.geturl(),
                request.allowed_hosts,
                request.allowed_origins,
                request.preserve_query,
            )
            status_code = int(response.getcode())
            content_type_header = response.headers.get("Content-Type", "application/octet-stream")
            content_type = content_type_header.split(";", 1)[0].strip().casefold()
            successful = 200 <= status_code < 300
            if (
                successful
                and "*" not in request.accepted_content_types
                and content_type not in request.accepted_content_types
            ):
                raise UnsupportedWebContentTypeError(content_type)
            body = response.read(request.max_response_bytes + 1) if successful else b""
            if len(body) > request.max_response_bytes:
                raise ResponseBudgetExceededError("wire_bytes_exceeded")
            content_encoding = response.headers.get("Content-Encoding", "").casefold()
            if content_encoding == "gzip" or body.startswith(b"\x1f\x8b"):
                body = _bounded_gzip_decompress(
                    body,
                    request.max_decompressed_bytes,
                    request.max_compression_ratio,
                )
            elif len(body) > request.max_decompressed_bytes:
                raise ResponseBudgetExceededError("decompressed_bytes_exceeded")
            headers = {
                key.casefold(): value
                for key in (
                    "Content-Type",
                    "Content-Encoding",
                    "ETag",
                    "Last-Modified",
                    "Retry-After",
                )
                if (value := response.headers.get(key)) is not None
            }
            return WebFetchResponse(
                requested_url=normalized_url,
                final_url=final_url,
                status_code=status_code,
                content_type=content_type_header,
                body=body,
                headers=headers,
            )


class _SafeRedirectHandler(HTTPRedirectHandler):
    def __init__(
        self,
        allowed_hosts: frozenset[str],
        allowed_origins: frozenset[str],
        preserve_query: bool,
        blocked_first_path_segments: frozenset[str],
    ) -> None:
        super().__init__()
        self._allowed_hosts = allowed_hosts
        self._allowed_origins = allowed_origins
        self._preserve_query = preserve_query
        self._blocked_first_path_segments = blocked_first_path_segments

    def redirect_request(self, request, file_pointer, code, message, headers, new_url):  # type: ignore[no-untyped-def]
        target = urljoin(request.full_url, new_url)
        normalized = _validate_public_url(
            target,
            self._allowed_hosts,
            self._allowed_origins,
            self._preserve_query,
        )
        if is_translated_url(normalized, tuple(self._blocked_first_path_segments)):
            raise PermissionError("redirect target uses a blocked language path")
        return super().redirect_request(request, file_pointer, code, message, headers, target)


class _PinnedHTTPConnection(HTTPConnection):
    def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self._create_connection = _create_public_connection


class _PinnedHTTPSConnection(HTTPSConnection):
    def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self._create_connection = _create_public_connection


class _PinnedHTTPHandler(HTTPHandler):
    def http_open(self, request):  # type: ignore[no-untyped-def]
        return self.do_open(_PinnedHTTPConnection, request)


class _PinnedHTTPSHandler(HTTPSHandler):
    def https_open(self, request):  # type: ignore[no-untyped-def]
        return self.do_open(_PinnedHTTPSConnection, request, context=self._context)


def _build_opener(
    allowed_hosts: frozenset[str],
    *,
    allowed_origins: frozenset[str] = frozenset(),
    preserve_query: bool = False,
    blocked_first_path_segments: frozenset[str] = frozenset(),
) -> OpenerDirector:
    return build_opener(
        ProxyHandler({}),
        _PinnedHTTPHandler(),
        _PinnedHTTPSHandler(),
        _SafeRedirectHandler(
            allowed_hosts,
            allowed_origins,
            preserve_query,
            blocked_first_path_segments,
        ),
    )


def _validate_public_url(
    value: str,
    allowed_hosts: frozenset[str],
    allowed_origins: frozenset[str] = frozenset(),
    preserve_query: bool = False,
) -> str:
    normalized = canonicalize_url(
        value,
        allowed_hosts=allowed_hosts,
        keep_xml=True,
        preserve_query=preserve_query,
    )
    normalized_origins = {origin_for_url(item) for item in allowed_origins}
    if normalized_origins and origin_for_url(normalized) not in normalized_origins:
        raise PermissionError("web URL origin is outside the configured crawl boundary")
    host = urlsplit(normalized).hostname
    if host is None:
        raise ValueError("web URL has no host")
    parsed = urlsplit(normalized)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    _resolve_public_addresses(host, port)
    return normalized


def _resolve_public_addresses(host: str, port: int):  # type: ignore[no-untyped-def]
    try:
        resolved = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise ConnectionError("web host could not be resolved") from error
    candidates = []
    seen = set()
    for family, socket_type, protocol, _canonical_name, socket_address in resolved:
        if not socket_address:
            continue
        address = ipaddress.ip_address(socket_address[0])
        if not address.is_global:
            raise PermissionError("web host resolved to a non-public address")
        candidate = (family, socket_type, protocol, socket_address)
        if candidate not in seen:
            candidates.append(candidate)
            seen.add(candidate)
    if not candidates:
        raise ConnectionError("web host resolved to no addresses")
    return tuple(candidates)


def _create_public_connection(
    address,  # type: ignore[no-untyped-def]
    timeout=socket._GLOBAL_DEFAULT_TIMEOUT,  # noqa: SLF001
    source_address=None,  # type: ignore[no-untyped-def]
):
    host, port = address
    errors: list[OSError] = []
    for family, socket_type, protocol, socket_address in _resolve_public_addresses(host, port):
        connection = None
        try:
            connection = socket.socket(family, socket_type, protocol)
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:  # noqa: SLF001
                connection.settimeout(timeout)
            if source_address:
                connection.bind(source_address)
            connection.connect(socket_address)
            return connection
        except OSError as error:
            errors.append(error)
            if connection is not None:
                connection.close()
    if errors:
        raise errors[-1]
    raise ConnectionError("web host resolved to no usable public addresses")


def _bounded_gzip_decompress(value: bytes, max_bytes: int, max_ratio: float) -> bytes:
    ratio_limit = max(1, int(len(value) * max_ratio))
    read_limit = min(max_bytes, ratio_limit)
    with gzip.GzipFile(fileobj=BytesIO(value)) as archive:
        result = archive.read(read_limit + 1)
    if len(result) > max_bytes:
        raise ResponseBudgetExceededError("decompressed_bytes_exceeded")
    if len(result) > ratio_limit:
        raise ResponseBudgetExceededError("compression_ratio_exceeded")
    return result
