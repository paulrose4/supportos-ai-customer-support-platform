import posixpath
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit

_TRACKING_PARAMETERS = frozenset(
    {
        "fbclid",
        "gclid",
        "mc_cid",
        "mc_eid",
        "ref",
        "referrer",
        "source",
    }
)
_IGNORED_EXTENSIONS = frozenset(
    {
        ".7z",
        ".avi",
        ".css",
        ".csv",
        ".doc",
        ".docx",
        ".gif",
        ".gz",
        ".ico",
        ".jpeg",
        ".jpg",
        ".js",
        ".json",
        ".m4a",
        ".mov",
        ".mp3",
        ".mp4",
        ".pdf",
        ".png",
        ".rar",
        ".rss",
        ".svg",
        ".tar",
        ".webm",
        ".webp",
        ".xml",
        ".zip",
    }
)


def canonicalize_url(
    value: str,
    *,
    base_url: str | None = None,
    allowed_hosts: frozenset[str] | None = None,
    keep_xml: bool = False,
    preserve_query: bool = False,
) -> str:
    absolute = urljoin(base_url, value) if base_url else value
    parsed = urlsplit(absolute.strip())
    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("URL must be an absolute HTTP or HTTPS URL without credentials")
    host = parsed.hostname.casefold().encode("idna").decode("ascii")
    normalized_hosts = {
        item.casefold().encode("idna").decode("ascii") for item in allowed_hosts or ()
    }
    if normalized_hosts and host not in normalized_hosts:
        raise ValueError("URL host is outside the configured crawl boundary")
    port = parsed.port
    authority_host = f"[{host}]" if ":" in host else host
    netloc = authority_host
    if port is not None and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        netloc = f"{authority_host}:{port}"
    raw_path = parsed.path or "/"
    normalized_path = posixpath.normpath(raw_path)
    if raw_path.endswith("/") and normalized_path != "/":
        normalized_path += "/"
    if not normalized_path.startswith("/"):
        normalized_path = "/" + normalized_path
    normalized_path = quote(normalized_path, safe="/%:@-._~!$&'()*+,;=")
    extension = posixpath.splitext(normalized_path.casefold())[1]
    if extension in _IGNORED_EXTENSIONS and not (keep_xml and extension in {".xml", ".gz"}):
        raise ValueError("URL points to an unsupported non-HTML resource")
    if preserve_query:
        normalized_query = parsed.query
    else:
        query = []
        for key, item_value in parse_qsl(parsed.query, keep_blank_values=True):
            normalized_key = key.casefold()
            if normalized_key.startswith("utm_") or normalized_key in _TRACKING_PARAMETERS:
                continue
            query.append((key, item_value))
        query.sort()
        normalized_query = urlencode(query, doseq=True)
    return urlunsplit((scheme, netloc, normalized_path, normalized_query, ""))


def host_for_url(value: str) -> str:
    parsed = urlsplit(value)
    if not parsed.hostname:
        raise ValueError("URL has no host")
    return parsed.hostname.casefold().encode("idna").decode("ascii")


def origin_for_url(value: str) -> str:
    parsed = urlsplit(value)
    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL has no HTTP origin")
    host = parsed.hostname.casefold().encode("idna").decode("ascii")
    authority_host = f"[{host}]" if ":" in host else host
    port = parsed.port
    if port is None or (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        return f"{scheme}://{authority_host}"
    return f"{scheme}://{authority_host}:{port}"
