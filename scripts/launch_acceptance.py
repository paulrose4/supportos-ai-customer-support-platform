import argparse
import getpass
import json
import os
import ssl
import time
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPCookieProcessor, HTTPSHandler, Request, build_opener
from uuid import uuid4

_SENSITIVE_KEY_PARTS = (
    "authorization",
    "cookie",
    "credential",
    "password",
    "secret",
    "site_key",
    "token",
    "api_key",
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run non-destructive launch acceptance checks against a deployed Dashboard."
    )
    parser.add_argument(
        "--base-url", required=True, help="Dashboard origin, e.g. https://support.example.com"
    )
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--password")
    parser.add_argument("--password-env", help="Read the password from this environment variable")
    parser.add_argument(
        "--site-key", help="Optional site key for widget credential and presence checks"
    )
    parser.add_argument("--require-production", action="store_true")
    parser.add_argument("--require-current-backups", action="store_true")
    parser.add_argument("--insecure-tls", action="store_true", help="Development only")
    return parser


class AcceptanceClient:
    def __init__(self, base_url: str, *, insecure_tls: bool) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.origin = self.base_url.rstrip("/")
        context = ssl._create_unverified_context() if insecure_tls else ssl.create_default_context()
        self._opener = build_opener(HTTPCookieProcessor(CookieJar()), HTTPSHandler(context=context))

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
        expected_status: int = 200,
    ) -> tuple[dict[str, object] | None, object]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request_headers = {"Accept": "application/json", **(headers or {})}
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        request = Request(
            urljoin(self.base_url, path.lstrip("/")),
            data=body,
            method=method,
            headers=request_headers,
        )
        try:
            response = self._opener.open(request, timeout=15)
            response_body = response.read()
            status = response.status
            response_headers = response.headers
        except HTTPError as exc:
            response_body = exc.read()
            status = exc.code
            response_headers = exc.headers
        except URLError as exc:
            raise RuntimeError(f"request failed for {path}: {exc.reason}") from exc
        if status != expected_status:
            detail = response_body.decode("utf-8", errors="replace")[:500]
            raise RuntimeError(
                f"{method} {path} returned {status}, expected {expected_status}: {detail}"
            )
        content_type = str(response_headers.get("Content-Type", ""))
        parsed = (
            json.loads(response_body)
            if response_body and "application/json" in content_type
            else None
        )
        return parsed, response_headers

    def wait_request(
        self,
        path: str,
        *,
        expected_status: int = 200,
        timeout_seconds: int = 90,
    ) -> tuple[dict[str, object] | None, object]:
        deadline = time.monotonic() + timeout_seconds
        last_error: RuntimeError | None = None
        while time.monotonic() < deadline:
            try:
                return self.request(path, expected_status=expected_status)
            except RuntimeError as exc:
                last_error = exc
                time.sleep(2)
        raise RuntimeError(f"timed out waiting for {path}: {last_error}")

    def unsafe_headers(self) -> dict[str, str]:
        return {"Origin": self.origin, "Sec-Fetch-Site": "same-origin"}


def _assert_audit_redaction(value: object, key: str = "") -> None:
    normalized_key = key.casefold()
    if normalized_key and any(part in normalized_key for part in _SENSITIVE_KEY_PARTS):
        if value != "[REDACTED]":
            raise RuntimeError(f"audit response contains an unredacted sensitive field: {key}")
        return
    if isinstance(value, dict):
        for item_key, item_value in value.items():
            _assert_audit_redaction(item_value, str(item_key))
    elif isinstance(value, list):
        for item in value:
            _assert_audit_redaction(item)


def _run(args: argparse.Namespace) -> dict[str, object]:
    parsed_url = urlparse(args.base_url)
    if args.require_production and parsed_url.scheme != "https":
        raise RuntimeError("production acceptance requires an HTTPS base URL")
    if args.insecure_tls and args.require_production:
        raise RuntimeError("--insecure-tls cannot be combined with --require-production")

    client = AcceptanceClient(args.base_url, insecure_tls=args.insecure_tls)
    checks: list[str] = []

    live, _ = client.wait_request("/health/live")
    if live is None or live.get("status") not in {"ok", "alive"}:
        raise RuntimeError("liveness response is invalid")
    checks.append("liveness")

    ready, _ = client.wait_request("/health/ready")
    if ready is None or ready.get("status") != "ready":
        raise RuntimeError("readiness response is invalid")
    checks.append("readiness")

    _, dashboard_headers = client.wait_request("/")
    if dashboard_headers.get("X-Frame-Options") != "DENY":
        raise RuntimeError("Dashboard X-Frame-Options must be DENY")
    if "frame-ancestors 'none'" not in str(dashboard_headers.get("Content-Security-Policy", "")):
        raise RuntimeError("Dashboard CSP must deny frame ancestors")
    checks.append("dashboard_security_headers")

    login, _ = client.request(
        "/v1/auth/login",
        method="POST",
        payload={
            "tenant_id": args.tenant_id,
            "username": args.username,
            "password": args.password,
        },
        headers=client.unsafe_headers(),
    )
    if login is None:
        raise RuntimeError("login response is empty")
    checks.append("administrator_login")

    me, _ = client.request("/v1/auth/me")
    user = me.get("user", {}) if me else {}
    if user.get("tenant_id") != args.tenant_id:
        raise RuntimeError("authenticated tenant does not match the requested tenant")
    checks.append("trusted_tenant_identity")

    status, _ = client.request("/v1/admin/system/status")
    if status is None or not status.get("is_ready"):
        raise RuntimeError("system status reports an unhealthy dependency")
    configuration = status.get("configuration", {})
    if args.require_production:
        expected = {
            "app_env": "production",
            "auth_mode": "session",
        }
        for key, value in expected.items():
            if configuration.get(key) != value:
                raise RuntimeError(f"production configuration mismatch: {key}")
        if (
            configuration.get("llm_provider") == "fake"
            or configuration.get("embedding_provider") == "fake"
        ):
            raise RuntimeError("production acceptance rejects fake model providers")
    if args.require_current_backups:
        backups = status.get("backups", [])
        states = {item.get("artifact_type"): item.get("state") for item in backups}
        if states != {"postgres": "current", "qdrant": "current"}:
            raise RuntimeError(f"backup freshness check failed: {states}")
    checks.append("system_status")

    sessions, _ = client.request("/v1/auth/sessions")
    session_items = sessions.get("items", []) if sessions else []
    if not any(item.get("is_current") for item in session_items):
        raise RuntimeError("current administrator session was not listed")
    checks.append("session_visibility")

    audits, _ = client.request("/v1/admin/audit-events?limit=50")
    for event in audits.get("items", []) if audits else []:
        _assert_audit_redaction(event.get("details", {}))
    checks.append("audit_access_and_redaction")

    if args.site_key:
        visitor_id = f"acceptance-{uuid4()}"
        client.request(
            "/v1/widget/presence",
            method="POST",
            payload={
                "visitor_id": visitor_id,
                "conversation_id": None,
                "page_path": "/launch-acceptance",
            },
            headers={"X-Agent-Site-Key": args.site_key},
        )
        client.request(
            "/v1/widget/presence",
            method="POST",
            payload={
                "visitor_id": visitor_id,
                "conversation_id": None,
                "page_path": "/launch-acceptance",
            },
            headers={"X-Agent-Site-Key": "invalid-acceptance-site-key"},
            expected_status=401,
        )
        checks.append("widget_site_credential")

    client.request(
        "/v1/auth/logout",
        method="POST",
        headers=client.unsafe_headers(),
        expected_status=204,
    )
    checks.append("administrator_logout")
    return {"status": "passed", "checks": checks, "check_count": len(checks)}


def main() -> int:
    args = _parser().parse_args()
    if args.password_env:
        args.password = os.getenv(args.password_env)
    if not args.password:
        args.password = getpass.getpass("Administrator password: ")
    try:
        result = _run(args)
    except RuntimeError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
