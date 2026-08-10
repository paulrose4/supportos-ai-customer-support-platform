from datetime import UTC, datetime, timedelta
from hashlib import sha256
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api.routes.identity import router
from app.application.services import (
    AdminLoginThrottledError,
    InvitationAccessError,
)
from app.config import Settings
from app.domain.models import AdminUser, AuthenticatedPrincipal, IssuedAdminSession


class FakeAdminSessionService:
    def __init__(self) -> None:
        self.login_command = None
        self.password_command = None
        self.throttle = False

    async def login(self, command):  # type: ignore[no-untyped-def]
        self.login_command = command
        if self.throttle:
            raise AdminLoginThrottledError(37)
        now = datetime.now(UTC)
        user = AdminUser(
            user_id="admin-1",
            tenant_id="tenant-a",
            username="owner",
            display_name="Owner",
            password_hash="not-exposed",
            roles=frozenset({"tenant_owner"}),
            scopes=frozenset({"users:manage"}),
            status="active",
            created_at=now,
            updated_at=now,
        )
        return SimpleNamespace(
            session=IssuedAdminSession(
                token="opaque-session-token",
                expires_at=now + timedelta(hours=1),
                user=user,
            )
        )

    async def change_password(self, command) -> None:  # type: ignore[no-untyped-def]
        self.password_command = command

    async def authenticate(self, command):  # type: ignore[no-untyped-def]
        now = datetime.now(UTC)
        return SimpleNamespace(
            principal=AuthenticatedPrincipal(
                subject_id="admin-1",
                tenant_id="tenant-a",
                roles=frozenset({"tenant_owner"}),
                scopes=frozenset({"users:manage"}),
                authentication_method="local",
                authenticated_at=now,
                correlation_id=command.correlation_id,
            )
        )


class FakeEmailIdentityService:
    def __init__(self, *, multiple_workspaces: bool = False) -> None:
        self.login_command = None
        self.registration_command = None
        self.multiple_workspaces = multiple_workspaces
        self.reject_registration = False

    async def login(self, command):  # type: ignore[no-untyped-def]
        self.login_command = command
        if self.multiple_workspaces:
            return SimpleNamespace(session=None, completion_token="workspace-grant-token")
        return SimpleNamespace(
            session=_issued_email_session(),
            completion_token=None,
        )

    async def register(self, command):  # type: ignore[no-untyped-def]
        self.registration_command = command
        if self.reject_registration:
            raise InvitationAccessError("invitation is no longer available")
        return SimpleNamespace(
            session=_issued_email_session(),
            completion_token=None,
        )


def _issued_email_session() -> IssuedAdminSession:
    now = datetime.now(UTC)
    return IssuedAdminSession(
        token="opaque-email-session-token",
        expires_at=now + timedelta(hours=1),
        user=AdminUser(
            user_id="email-user-1",
            tenant_id="tenant-a",
            username="owner@example.com",
            display_name="Email Owner",
            password_hash="",
            roles=frozenset({"tenant_owner"}),
            scopes=frozenset({"users:manage"}),
            status="active",
            created_at=now,
            updated_at=now,
            authentication_method="email_password",
        ),
    )


async def _correlation_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
    request.state.correlation_id = "correlation-test"
    return await call_next(request)


def _client(
    service: FakeAdminSessionService,
    *,
    email_service: FakeEmailIdentityService | None = None,
    settings: Settings | None = None,
) -> TestClient:
    application = FastAPI()
    application.middleware("http")(_correlation_middleware)
    application.include_router(router)
    application.state.container = SimpleNamespace(
        settings=settings
        or Settings(
            auth_mode="session",
            admin_cookie_secure=False,
            seed_mock_business_data=False,
            legacy_login_enabled=True,
        ),
        admin_session_service=service,
        email_identity_service=email_service,
        external_identity_service=None,
    )
    return TestClient(application)


def test_login_maps_trusted_source_and_sets_opaque_cookie() -> None:
    service = FakeAdminSessionService()
    with _client(service) as client:
        response = client.post(
            "/v1/auth/login",
            json={"tenant_id": "tenant-a", "username": "owner", "password": "password"},
        )

    assert response.status_code == 200
    assert service.login_command.source_fingerprint == sha256(b"testclient").hexdigest()
    assert "opaque-session-token" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]


def test_login_throttle_returns_retry_after_without_identity_detail() -> None:
    service = FakeAdminSessionService()
    service.throttle = True
    with _client(service) as client:
        response = client.post(
            "/v1/auth/login",
            json={"tenant_id": "tenant-a", "username": "owner", "password": "wrong"},
        )

    assert response.status_code == 429
    assert response.headers["retry-after"] == "37"
    assert response.json() == {"detail": "too many login attempts; try again later"}


def test_password_change_uses_session_cookie_and_deletes_it() -> None:
    service = FakeAdminSessionService()
    with _client(service) as client:
        client.cookies.set("support_admin_session", "current-session")
        response = client.post(
            "/v1/auth/change-password",
            json={
                "current_password": "current-password",
                "new_password": "new-secure-password",
            },
        )

    assert response.status_code == 204
    assert service.password_command.session_token == "current-session"
    assert service.password_command.correlation_id == "correlation-test"
    assert "support_admin_session=" in response.headers["set-cookie"]
    assert "Max-Age=0" in response.headers["set-cookie"]


def test_email_login_sets_session_cookie_without_accepting_tenant_id() -> None:
    legacy_service = FakeAdminSessionService()
    email_service = FakeEmailIdentityService()
    with _client(legacy_service, email_service=email_service) as client:
        response = client.post(
            "/v1/auth/email/login",
            json={"email": "Owner@Example.com", "password": "correct-password"},
        )

    assert response.status_code == 200
    assert email_service.login_command.email == "Owner@Example.com"
    assert not hasattr(email_service.login_command, "tenant_id")
    assert response.json()["workspace_selection_required"] is False
    assert response.json()["user"]["authentication_method"] == "email_password"
    assert "opaque-email-session-token" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]


def test_email_login_sets_short_lived_workspace_completion_cookie() -> None:
    legacy_service = FakeAdminSessionService()
    email_service = FakeEmailIdentityService(multiple_workspaces=True)
    with _client(legacy_service, email_service=email_service) as client:
        response = client.post(
            "/v1/auth/email/login",
            json={"email": "owner@example.com", "password": "correct-password"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "user": None,
        "expires_at": None,
        "workspace_selection_required": True,
    }
    cookie = response.headers["set-cookie"]
    assert "support_login_completion=workspace-grant-token" in cookie
    assert "Path=/v1/auth" in cookie
    assert "HttpOnly" in cookie


def test_provider_flags_expose_email_invitation_and_reset_availability() -> None:
    settings = Settings(
        auth_mode="session",
        admin_cookie_secure=False,
        seed_mock_business_data=False,
        email_login_enabled=True,
        invite_registration_enabled=True,
        self_service_signup_enabled=False,
        transactional_email_enabled=True,
        legacy_login_enabled=True,
        smtp_host="smtp.example.com",
        smtp_from_address="support@example.com",
    )
    with _client(FakeAdminSessionService(), settings=settings) as client:
        response = client.get("/v1/auth/providers")

    assert response.status_code == 200
    assert response.json() == {
        "providers": [],
        "legacy_login_enabled": True,
        "email_login_enabled": True,
        "invite_registration_enabled": True,
        "self_service_signup_enabled": False,
        "password_reset_enabled": True,
    }


def test_redeemed_invitation_returns_controlled_registration_error() -> None:
    legacy_service = FakeAdminSessionService()
    email_service = FakeEmailIdentityService()
    email_service.reject_registration = True
    with _client(legacy_service, email_service=email_service) as client:
        response = client.post(
            "/v1/auth/register",
            json={
                "invitation_token": "x" * 48,
                "display_name": "Invited User",
                "password": "secure-password",
            },
        )

    assert response.status_code == 400
    assert response.json() == {"detail": "invitation is no longer available"}
