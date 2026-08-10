from dataclasses import dataclass, field
from typing import Protocol


class UnsupportedWebContentTypeError(ValueError):
    def __init__(self, content_type: str) -> None:
        self.content_type = content_type
        super().__init__(f"unsupported response content type: {content_type}")


class ResponseBudgetExceededError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code.replace("_", " "))


class WebTransportError(ConnectionError):
    pass


@dataclass(frozen=True, slots=True)
class WebFetchRequest:
    url: str
    allowed_hosts: frozenset[str]
    allowed_origins: frozenset[str] = field(default_factory=frozenset)
    preserve_query: bool = False
    timeout_seconds: float = 15.0
    max_response_bytes: int = 2_000_000
    max_decompressed_bytes: int = 4_000_000
    max_compression_ratio: float = 50.0
    user_agent: str = "CompanyProductSupportKnowledgeCrawler/0.1"
    if_none_match: str | None = None
    if_modified_since: str | None = None
    blocked_first_path_segments: frozenset[str] = field(default_factory=frozenset)
    accepted_content_types: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {
                "text/html",
                "application/xhtml+xml",
                "application/xml",
                "text/xml",
                "text/plain",
                "application/gzip",
                "application/x-gzip",
            }
        )
    )


@dataclass(frozen=True, slots=True)
class WebFetchResponse:
    requested_url: str
    final_url: str
    status_code: int
    content_type: str
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)


class WebPageFetcherPort(Protocol):
    async def fetch(self, request: WebFetchRequest) -> WebFetchResponse: ...
