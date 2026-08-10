from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.application.dto import (
    ListPlatformOnboardingRecordsQuery,
    ListPlatformSiteRecordsQuery,
    ListPlatformTenantRecordsQuery,
    RevokePlatformRoleCommand,
)
from app.application.services.platform_identity import PlatformIdentityService
from app.domain.models import (
    AuthenticatedPrincipal,
    PlatformOnboardingSourceRecord,
    PlatformSiteRecord,
    PlatformTenantRecord,
)


def _principal(*roles: str) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        subject_id="platform-user",
        tenant_id="tenant-demo",
        roles=frozenset({"tenant_owner"}),
        scopes=frozenset(),
        authentication_method="email_password",
        authenticated_at=datetime.now(UTC),
        correlation_id="test-correlation",
        platform_roles=frozenset(roles),
    )


class PlatformStoreStub:
    def __init__(self) -> None:
        self.onboarding: list[PlatformOnboardingSourceRecord] = []
        self.revoked: tuple[str, str] | None = None
        self.sites: list[PlatformSiteRecord] = []
        self.site_query: dict[str, object] | None = None
        self.site_has_more = False

    async def list_platform_onboarding_records(
        self, *, search: str
    ) -> list[PlatformOnboardingSourceRecord]:
        return [item for item in self.onboarding if search.casefold() in item.target_email]

    async def list_platform_tenant_records(
        self,
        *,
        search: str,
        status: str,
        limit: int,
        after_updated_at: datetime | None,
        after_tenant_id: str | None,
    ) -> tuple[list[PlatformTenantRecord], int, bool]:
        del search, status, limit, after_updated_at, after_tenant_id
        return [], 0, False

    async def list_platform_site_records(self, **query):  # type: ignore[no-untyped-def]
        self.site_query = query
        return self.sites, len(self.sites), self.site_has_more

    async def revoke_platform_role(
        self,
        *,
        user_id: str,
        role: str,
        actor_subject_id: str,
        correlation_id: str,
        revoked_at: datetime,
    ) -> None:
        del actor_subject_id, correlation_id, revoked_at
        self.revoked = (user_id, role)


def _source(
    *,
    code_id: str,
    code_status: str = "issued",
    intent_status: str | None = None,
    email_status: str | None = None,
    expires_delta: timedelta = timedelta(hours=72),
) -> PlatformOnboardingSourceRecord:
    now = datetime.now(UTC)
    return PlatformOnboardingSourceRecord(
        code_id=code_id,
        policy_id="policy",
        target_email=f"{code_id}@example.com",
        code_status=code_status,
        expires_at=now + expires_delta,
        created_by="platform-user",
        created_by_name="Platform Owner",
        created_at=now,
        intent_status=intent_status,
        workspace_name="Example Workspace" if intent_status else None,
        proposed_tenant_id="tenant-example" if intent_status else None,
        intent_expires_at=now + expires_delta if intent_status else None,
        completed_at=now if intent_status == "completed" else None,
        email_status=email_status,
        email_attempts=2 if email_status else None,
        email_sent_at=now if email_status == "sent" else None,
        email_last_error="smtp_delivery_failed" if email_status == "failed" else None,
    )


def _site(
    *,
    tenant_id: str = "tenant-example",
    site_id: str = "storefront",
    verification_status: str = "verified",
    verification_expires_at: datetime | None = None,
) -> PlatformSiteRecord:
    now = datetime.now(UTC)
    return PlatformSiteRecord(
        tenant_id=tenant_id,
        tenant_name="Example Workspace",
        site_id=site_id,
        name="Example Store",
        base_url="https://shop.example.com",
        status="active",
        verification_status=verification_status,
        knowledge_publication_state="active",
        manager_names=("Owner A", "Owner B"),
        manager_emails=("owner-a@example.com", "owner-b@example.com"),
        created_at=now,
        updated_at=now,
        verification_expires_at=verification_expires_at,
    )


@pytest.mark.asyncio
async def test_onboarding_records_map_complete_operator_lifecycle() -> None:
    store = PlatformStoreStub()
    store.onboarding = [
        _source(code_id="issued"),
        _source(code_id="pending", code_status="reserved", intent_status="created"),
        _source(code_id="failed", intent_status="created", email_status="failed"),
        _source(code_id="completed", code_status="consumed", intent_status="completed"),
        _source(code_id="expired", expires_delta=timedelta(hours=-1)),
        _source(code_id="revoked", code_status="revoked"),
    ]
    service = PlatformIdentityService(store, store)  # type: ignore[arg-type]

    result = await service.list_onboarding_records(
        ListPlatformOnboardingRecordsQuery(principal=_principal("platform_operator"), limit=100)
    )

    assert {item.code_id: item.status for item in result.items} == {
        "issued": "issued",
        "pending": "verification_pending",
        "failed": "failed",
        "completed": "completed",
        "expired": "expired",
        "revoked": "revoked",
    }
    assert next(item for item in result.items if item.code_id == "completed").tenant_id == (
        "tenant-example"
    )


@pytest.mark.asyncio
async def test_onboarding_total_remains_stable_across_cursor_pages() -> None:
    store = PlatformStoreStub()
    store.onboarding = [_source(code_id="newer"), _source(code_id="older")]
    store.onboarding[1] = replace(
        store.onboarding[1],
        created_at=store.onboarding[1].created_at - timedelta(minutes=1),
    )
    service = PlatformIdentityService(store, store)  # type: ignore[arg-type]

    first = await service.list_onboarding_records(
        ListPlatformOnboardingRecordsQuery(principal=_principal("platform_auditor"), limit=1)
    )
    second = await service.list_onboarding_records(
        ListPlatformOnboardingRecordsQuery(
            principal=_principal("platform_auditor"), limit=1, cursor=first.next_cursor
        )
    )

    assert first.total == 2
    assert second.total == 2


@pytest.mark.asyncio
async def test_platform_query_rejects_invalid_cursor_before_store_access() -> None:
    store = PlatformStoreStub()
    service = PlatformIdentityService(store, store)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="cursor is invalid"):
        await service.list_tenant_records(
            ListPlatformTenantRecordsQuery(
                principal=_principal("platform_auditor"), cursor="not-a-cursor"
            )
        )
    with pytest.raises(ValueError, match="cursor is invalid"):
        await service.list_tenant_records(
            ListPlatformTenantRecordsQuery(principal=_principal("platform_auditor"), cursor="A")
        )


@pytest.mark.asyncio
async def test_platform_site_directory_allows_all_platform_roles_and_rejects_tenant_admin() -> None:
    store = PlatformStoreStub()
    store.sites = [_site()]
    service = PlatformIdentityService(store, store)  # type: ignore[arg-type]

    for role in ("platform_owner", "platform_operator", "platform_auditor"):
        result = await service.list_site_records(
            ListPlatformSiteRecordsQuery(principal=_principal(role))
        )
        assert result.items == tuple(store.sites)

    with pytest.raises(PermissionError, match="platform administration"):
        await service.list_site_records(ListPlatformSiteRecordsQuery(principal=_principal()))


@pytest.mark.asyncio
async def test_platform_site_directory_normalizes_and_forwards_safe_filters() -> None:
    store = PlatformStoreStub()
    store.sites = [_site()]
    service = PlatformIdentityService(store, store)  # type: ignore[arg-type]

    await service.list_site_records(
        ListPlatformSiteRecordsQuery(
            principal=_principal("platform_auditor"),
            search="  example  ",
            tenant_id="  tenant-example  ",
            status="ACTIVE",
            verification_status="VERIFIED",
            include_disabled=False,
            limit=10,
        )
    )

    assert store.site_query is not None
    forwarded = dict(store.site_query)
    checked_at = forwarded.pop("checked_at")
    assert isinstance(checked_at, datetime)
    assert checked_at.tzinfo is not None
    assert forwarded == {
        "search": "example",
        "tenant_id": "tenant-example",
        "status": "active",
        "verification_status": "verified",
        "include_disabled": False,
        "limit": 10,
        "after_tenant_id": None,
        "after_site_id": None,
    }


@pytest.mark.asyncio
async def test_platform_site_directory_derives_expired_pending_challenges() -> None:
    store = PlatformStoreStub()
    store.sites = [
        _site(
            verification_status="pending",
            verification_expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
    ]
    service = PlatformIdentityService(store, store)  # type: ignore[arg-type]

    result = await service.list_site_records(
        ListPlatformSiteRecordsQuery(
            principal=_principal("platform_auditor"),
            verification_status="expired",
        )
    )

    assert result.items[0].verification_status == "expired"
    assert store.site_query is not None
    assert store.site_query["verification_status"] == "expired"


@pytest.mark.asyncio
async def test_platform_site_directory_rejects_invalid_filters_and_cursor() -> None:
    store = PlatformStoreStub()
    service = PlatformIdentityService(store, store)  # type: ignore[arg-type]
    principal = _principal("platform_auditor")

    with pytest.raises(ValueError, match="verification status"):
        await service.list_site_records(
            ListPlatformSiteRecordsQuery(
                principal=principal,
                verification_status="not-a-status",
            )
        )
    with pytest.raises(ValueError, match="cursor is invalid"):
        await service.list_site_records(
            ListPlatformSiteRecordsQuery(principal=principal, cursor="not-a-cursor")
        )
    with pytest.raises(ValueError, match="tenant_id must not be empty"):
        await service.list_site_records(
            ListPlatformSiteRecordsQuery(principal=principal, tenant_id="   ")
        )
    with pytest.raises(ValueError, match="cursor is invalid"):
        await service.list_site_records(
            ListPlatformSiteRecordsQuery(principal=principal, cursor="A")
        )
    with pytest.raises(ValueError, match="cursor is invalid"):
        await service.list_site_records(
            ListPlatformSiteRecordsQuery(
                principal=principal,
                cursor="WyBudWxsLCJzaXRlIl0",
            )
        )


@pytest.mark.asyncio
async def test_platform_site_cursor_preserves_workspace_and_site_identity() -> None:
    store = PlatformStoreStub()
    store.sites = [_site(tenant_id="tenant-a", site_id="shared-id")]
    store.site_has_more = True
    service = PlatformIdentityService(store, store)  # type: ignore[arg-type]
    principal = _principal("platform_auditor")

    first = await service.list_site_records(
        ListPlatformSiteRecordsQuery(principal=principal, limit=1)
    )
    assert first.next_cursor is not None

    store.site_has_more = False
    await service.list_site_records(
        ListPlatformSiteRecordsQuery(
            principal=principal,
            limit=1,
            cursor=first.next_cursor,
        )
    )

    assert store.site_query is not None
    assert store.site_query["after_tenant_id"] == "tenant-a"
    assert store.site_query["after_site_id"] == "shared-id"


@pytest.mark.asyncio
async def test_platform_site_cursor_is_bound_to_the_workspace_filter() -> None:
    store = PlatformStoreStub()
    store.sites = [_site(tenant_id="tenant-a", site_id="shared-id")]
    store.site_has_more = True
    service = PlatformIdentityService(store, store)  # type: ignore[arg-type]
    principal = _principal("platform_auditor")
    first = await service.list_site_records(
        ListPlatformSiteRecordsQuery(principal=principal, tenant_id="tenant-a", limit=1)
    )

    with pytest.raises(ValueError, match="requested workspace"):
        await service.list_site_records(
            ListPlatformSiteRecordsQuery(
                principal=principal,
                tenant_id="tenant-b",
                cursor=first.next_cursor,
                limit=1,
            )
        )


@pytest.mark.asyncio
async def test_only_platform_owner_can_revoke_platform_role() -> None:
    store = PlatformStoreStub()
    service = PlatformIdentityService(store, store)  # type: ignore[arg-type]

    with pytest.raises(PermissionError, match="platform owner"):
        await service.revoke_platform_role(
            RevokePlatformRoleCommand(
                principal=_principal("platform_operator"),
                user_id="user-a",
                role="platform_auditor",
                correlation_id="test-correlation",
            )
        )

    await service.revoke_platform_role(
        RevokePlatformRoleCommand(
            principal=_principal("platform_owner"),
            user_id="user-a",
            role="platform_auditor",
            correlation_id="test-correlation",
        )
    )
    assert store.revoked == ("user-a", "platform_auditor")
