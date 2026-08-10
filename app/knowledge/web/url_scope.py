from pathlib import PurePosixPath
from urllib.parse import urlsplit

_EXCLUDED_PATH_SEGMENTS = frozenset(
    {
        "account",
        "admin",
        "cart",
        "checkout",
        ".well-known",
        "login",
        "logout",
        "register",
        "search",
        "signin",
        "signup",
        "wp-admin",
        "wp-login.php",
    }
)

_UNSUPPORTED_DOCUMENT_SUFFIXES = frozenset(
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
        ".md",
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
        ".txt",
        ".webm",
        ".webp",
        ".xls",
        ".xlsx",
        ".xml",
        ".zip",
    }
)


def is_excluded_web_url(value: str) -> bool:
    parsed = urlsplit(value)
    segments = {segment.casefold() for segment in parsed.path.split("/") if segment}
    suffix = PurePosixPath(parsed.path).suffix.casefold()
    query = parsed.query.casefold()
    return (
        bool(segments & _EXCLUDED_PATH_SEGMENTS)
        or suffix in _UNSUPPORTED_DOCUMENT_SUFFIXES
        or "add-to-cart=" in query
    )
