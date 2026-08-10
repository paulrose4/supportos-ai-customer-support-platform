import re
from collections.abc import Iterable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_PUBLIC_WIDGET_BOT_PATTERN = re.compile(
    r"(?:googlebot|bingbot|yandexbot|baiduspider|bytespider|petalbot|duckduckbot|"
    r"gptbot|chatgpt-user|claudebot|claude-web|anthropic-ai|ccbot|perplexitybot|"
    r"amazonbot|cohere-ai|meta-externalagent|slurp|semrushbot|ahrefsbot|mj12bot|"
    r"dotbot|facebookexternalhit|twitterbot|linkedinbot|applebot|headlesschrome|"
    r"(?:bot|crawler|spider)(?:[\s/;:_-]|$))",
    re.IGNORECASE,
)


class BrowserSecurityMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,  # type: ignore[no-untyped-def]
        *,
        admin_cookie_name: str,
        login_completion_cookie_name: str | None = None,
        allowed_origins: Iterable[str],
        enforce_origin: bool,
    ) -> None:
        super().__init__(app)
        self._protected_cookie_names = frozenset(
            name for name in (admin_cookie_name, login_completion_cookie_name) if name
        )
        self._allowed_origins = frozenset(origin.rstrip("/") for origin in allowed_origins)
        self._enforce_origin = enforce_origin

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        if self._requires_origin_validation(request):
            origin = request.headers.get("origin", "").rstrip("/")
            fetch_site = request.headers.get("sec-fetch-site", "")
            if not origin or origin not in self._allowed_origins or fetch_site == "cross-site":
                return _secured_json_response(
                    status_code=403,
                    content={"detail": "trusted browser origin is required"},
                )
        response = await call_next(request)
        _apply_security_headers(response.headers, sensitive=_is_sensitive_path(request.url.path))
        return response

    def _requires_origin_validation(self, request: Request) -> bool:
        return (
            self._enforce_origin
            and request.method in _UNSAFE_METHODS
            and bool(self._protected_cookie_names.intersection(request.cookies))
        )


class PublicWidgetCorsIsolationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        if request.url.path.startswith("/v1/public-widget") and is_automated_user_agent(
            request.headers.get("user-agent")
        ):
            return _secured_json_response(
                status_code=403,
                content={"detail": "public widget access is denied"},
            )
        response = await call_next(request)
        if request.url.path.startswith("/v1/public-widget"):
            if "access-control-allow-credentials" in response.headers:
                del response.headers["access-control-allow-credentials"]
        return response


def is_automated_user_agent(user_agent: str | None) -> bool:
    return bool(user_agent and _PUBLIC_WIDGET_BOT_PATTERN.search(user_agent))


def websocket_origin_is_trusted(origin: str | None, allowed_origins: Iterable[str]) -> bool:
    if not origin:
        return False
    trusted = frozenset(value.rstrip("/") for value in allowed_origins)
    return origin.rstrip("/") in trusted


def _secured_json_response(*, status_code: int, content: dict[str, str]) -> JSONResponse:
    response = JSONResponse(status_code=status_code, content=content)
    _apply_security_headers(response.headers, sensitive=True)
    return response


def _apply_security_headers(headers, *, sensitive: bool) -> None:  # type: ignore[no-untyped-def]
    headers["X-Content-Type-Options"] = "nosniff"
    headers["X-Frame-Options"] = "DENY"
    headers["Referrer-Policy"] = "no-referrer"
    headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    headers["Content-Security-Policy"] = (
        "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    )
    if sensitive:
        headers["Cache-Control"] = "no-store, max-age=0"
        headers["Pragma"] = "no-cache"


def _is_sensitive_path(path: str) -> bool:
    return path.startswith(("/v1/auth", "/v1/admin", "/v1/handoffs", "/v1/chat"))
