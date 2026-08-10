from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api.routes.platform_identity import router
from app.application.dto import PlatformSiteRecordsResult
from app.application.services.platform_identity import PlatformIdentityService
from app.config import Settings
from app.domain.models import AuthenticatedPrincipal, PlatformSiteRecord


class FakeAdminSessionService:
    async def authenticate(self, command):  # type: ignore[no-untyped-def]
        return SimpleNamespace(
            principal=AuthenticatedPrincipal(
                subject_id="viewer-a",
                tenant_id="tenant-a",
                roles=frozenset({"support_agent"}),
                scopes=frozenset(),
                authentication_method="email_password",
                authenticated_at=datetime.now(UTC),
                correlation_id=command.correlation_id,
            )
        )


class RejectingInvitationService:
    async def create_invitation(self, command):  # type: ignore[no-untyped-def]
        del command
        raise PermissionError("workspace member management permission is required")


class PlatformAdminSessionService:
    def __init__(self, *platform_roles: str) -> None:
        self._platform_roles = frozenset(platform_roles)

    async def authenticate(self, command):  # type: ignore[no-untyped-def]
        return SimpleNamespace(
            principal=AuthenticatedPrincipal(
                subject_id="platform-viewer",
                tenant_id="tenant-a",
                roles=frozenset({"tenant_owner"}),
                scopes=frozenset(),
                authentication_method="email_password",
                authenticated_at=datetime.now(UTC),
                correlation_id=command.correlation_id,
                platform_roles=self._platform_roles,
            )
        )


class PlatformSiteService:
    def __init__(self) -> None:
        self.query = None

    async def list_site_records(self, query):  # type: ignore[no-untyped-def]
        self.query = query
        now = datetime.now(UTC)
        return PlatformSiteRecordsResult(
            items=(
                PlatformSiteRecord(
                    tenant_id="tenant-b",
                    tenant_name="Workspace B",
                    site_id="storefront",
                    name="Store B",
                    base_url="https://b.example.com",
                    status="disabled",
                    verification_status="pending",
                    knowledge_publication_state="active",
                    manager_names=("Owner B",),
                    manager_emails=("owner-b@example.com",),
                    created_at=now,
                    updated_at=now,
                ),
            ),
            total=1,
            next_cursor="next-page",
        )


def _platform_application(*, admin_session_service, platform_identity_service) -> FastAPI:
    application = FastAPI()
    application.middleware("http")(_correlation_middleware)
    application.include_router(router)
    application.state.container = SimpleNamespace(
        settings=Settings(
            auth_mode="session",
            admin_cookie_secure=False,
            seed_mock_business_data=False,
        ),
        admin_session_service=admin_session_service,
        invitation_management_service=None,
        platform_identity_service=platform_identity_service,
    )
    return application


async def _correlation_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
    request.state.correlation_id = "correlation-test"
    return await call_next(request)


def test_invitation_creation_requires_workspace_management_permission() -> None:
    application = FastAPI()
    application.middleware("http")(_correlation_middleware)
    application.include_router(router)
    application.state.container = SimpleNamespace(
        settings=Settings(
            auth_mode="session",
            admin_cookie_secure=False,
            seed_mock_business_data=False,
        ),
        admin_session_service=FakeAdminSessionService(),
        invitation_management_service=RejectingInvitationService(),
        platform_identity_service=None,
    )

    with TestClient(application) as client:
        client.cookies.set("support_admin_session", "session-token")
        response = client.post(
            "/v1/platform/tenants/tenant-a/invitations",
            json={
                "email": "new.user@example.com",
                "roles": ["support_agent"],
                "expires_in_hours": 72,
            },
        )

    assert response.status_code == 403
    assert response.json() == {"detail": "workspace member management permission is required"}


def test_platform_site_directory_maps_filters_and_returns_only_sanitized_fields() -> None:
    service = PlatformSiteService()
    application = _platform_application(
        admin_session_service=PlatformAdminSessionService("platform_auditor"),
        platform_identity_service=service,
    )

    with TestClient(application) as client:
        client.cookies.set("support_admin_session", "session-token")
        response = client.get(
            "/v1/platform/sites",
            params={
                "q": "store",
                "tenant_id": "tenant-b",
                "status": "disabled",
                "verification_status": "pending",
                "include_disabled": "true",
                "limit": "10",
            },
        )

    assert response.status_code == 200
    assert service.query.search == "store"
    assert service.query.tenant_id == "tenant-b"
    assert service.query.status == "disabled"
    assert service.query.verification_status == "pending"
    assert response.json()["total"] == 1
    item = response.json()["items"][0]
    assert item["tenant_name"] == "Workspace B"
    assert item["manager_emails"] == ["owner-b@example.com"]
    assert not {
        "site_key",
        "key_hash",
        "verification_token",
        "verification_token_hash",
        "verification_expires_at",
        "install_code",
        "public_widget_id",
        "primary_language",
        "manager_user_ids",
    }.intersection(item)


def test_platform_tenant_site_route_forces_the_path_workspace() -> None:
    service = PlatformSiteService()
    application = _platform_application(
        admin_session_service=PlatformAdminSessionService("platform_operator"),
        platform_identity_service=service,
    )

    with TestClient(application) as client:
        client.cookies.set("support_admin_session", "session-token")
        response = client.get(
            "/v1/platform/tenants/tenant-b/sites",
            params={"tenant_id": "tenant-a"},
        )

    assert response.status_code == 200
    assert service.query.tenant_id == "tenant-b"


def test_tenant_admin_cannot_read_the_platform_site_directory() -> None:
    application = _platform_application(
        admin_session_service=PlatformAdminSessionService(),
        platform_identity_service=PlatformIdentityService(
            SimpleNamespace(),
            SimpleNamespace(),
        ),
    )

    with TestClient(application) as client:
        client.cookies.set("support_admin_session", "session-token")
        response = client.get("/v1/platform/sites")

    assert response.status_code == 403
    assert response.json() == {"detail": "platform administration permission is required"}
