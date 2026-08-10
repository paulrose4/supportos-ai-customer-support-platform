from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.middleware import BrowserSecurityMiddleware, websocket_origin_is_trusted


def client() -> TestClient:
    application = FastAPI()
    application.add_middleware(
        BrowserSecurityMiddleware,
        admin_cookie_name="support_admin_session",
        allowed_origins=["https://support.example.com"],
        enforce_origin=True,
    )

    @application.get("/v1/admin/example")
    async def read_example() -> dict[str, str]:
        return {"status": "ok"}

    @application.post("/v1/admin/example")
    async def write_example() -> dict[str, str]:
        return {"status": "updated"}

    return TestClient(application)


def test_sensitive_responses_receive_security_and_no_store_headers() -> None:
    with client() as test_client:
        response = test_client.get("/v1/admin/example")

    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert "default-src 'none'" in response.headers["content-security-policy"]


def test_cookie_authenticated_write_requires_trusted_origin() -> None:
    with client() as test_client:
        test_client.cookies.set("support_admin_session", "opaque-session")
        missing = test_client.post("/v1/admin/example")
        untrusted = test_client.post(
            "/v1/admin/example",
            headers={"Origin": "https://attacker.example", "Sec-Fetch-Site": "cross-site"},
        )
        trusted = test_client.post(
            "/v1/admin/example",
            headers={"Origin": "https://support.example.com", "Sec-Fetch-Site": "same-origin"},
        )

    assert missing.status_code == 403
    assert untrusted.status_code == 403
    assert trusted.status_code == 200


def test_non_cookie_server_to_server_write_is_not_treated_as_browser_csrf() -> None:
    with client() as test_client:
        response = test_client.post("/v1/admin/example")

    assert response.status_code == 200


def test_websocket_origin_matching_is_exact() -> None:
    origins = ["https://support.example.com"]

    assert websocket_origin_is_trusted("https://support.example.com", origins)
    assert not websocket_origin_is_trusted(None, origins)
    assert not websocket_origin_is_trusted("https://attacker.example", origins)
    assert not websocket_origin_is_trusted("https://support.example.com.attacker.test", origins)
