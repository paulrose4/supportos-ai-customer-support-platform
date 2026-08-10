from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.application.dto import (
    CreateManagedSiteCommand,
    ListManagedSitesQuery,
    RotateManagedSiteKeyCommand,
    UpdateManagedSiteCommand,
)
from app.application.services import (
    SiteAdministrationConflictError,
    SiteAdministrationService,
)
from app.application.services.site_admin import hash_site_key
from app.domain.models import AuthenticatedPrincipal, ManagedSupportSite
from app.domain.rules.rbac import scopes_for_roles


class InMemorySiteAdministrationPort:
    def __init__(self) -> None:
        self.sites: dict[tuple[str, str], ManagedSupportSite] = {}
        self.hashes: dict[tuple[str, str], str] = {}
        self.audit_events: list[str] = []

    async def list_managed_sites(self, *, tenant_id: str):  # type: ignore[no-untyped-def]
        return [item for item in self.sites.values() if item.tenant_id == tenant_id]

    async def create_managed_site(
        self,
        *,
        tenant_id: str,
        site_id: str,
        public_widget_id: str,
        name: str,
        base_url: str,
        allowed_origins: tuple[str, ...],
        widget_daily_message_limit: int,
        primary_language: str,
        key_hash: str | None,
        key_prefix: str | None,
        actor_subject_id: str,
        correlation_id: str,
        created_at: datetime,
    ):  # type: ignore[no-untyped-def]
        del actor_subject_id, correlation_id
        key = (tenant_id, site_id)
        existing = self.sites.get(key)
        if existing is not None:
            if (
                existing.name == name
                and existing.base_url == base_url
                and existing.status == "active"
                and existing.primary_language == primary_language
                and self.hashes[key] == key_hash
            ):
                return existing
            return None
        site = ManagedSupportSite(
            site_id=site_id,
            tenant_id=tenant_id,
            public_widget_id=public_widget_id,
            name=name,
            base_url=base_url,
            allowed_origins=allowed_origins,
            widget_daily_message_limit=widget_daily_message_limit,
            status="active",
            credential_key_prefix=key_prefix,
            credential_status="active",
            created_at=created_at,
            updated_at=created_at,
            primary_language=primary_language,
        )
        self.sites[key] = site
        if key_hash is not None:
            self.hashes[key] = key_hash
        self.audit_events.append("support_site.created")
        return site

    async def update_managed_site(
        self,
        *,
        tenant_id: str,
        site_id: str,
        name: str,
        base_url: str,
        allowed_origins: tuple[str, ...],
        status: str,
        primary_language: str,
        actor_subject_id: str,
        correlation_id: str,
        changed_at: datetime,
    ):  # type: ignore[no-untyped-def]
        del actor_subject_id, correlation_id
        key = (tenant_id, site_id)
        current = self.sites.get(key)
        if current is None:
            return None
        if (
            current.name == name
            and current.base_url == base_url
            and current.status == status
            and current.primary_language == primary_language
        ):
            return current
        updated = replace(
            current,
            name=name,
            base_url=base_url,
            allowed_origins=allowed_origins,
            status=status,
            primary_language=primary_language,
            updated_at=changed_at,
        )
        self.sites[key] = updated
        self.audit_events.append("support_site.updated")
        return updated

    async def rotate_site_key(
        self,
        *,
        tenant_id: str,
        site_id: str,
        key_hash: str,
        key_prefix: str,
        actor_subject_id: str,
        correlation_id: str,
        changed_at: datetime,
    ):  # type: ignore[no-untyped-def]
        del actor_subject_id, correlation_id
        key = (tenant_id, site_id)
        current = self.sites.get(key)
        if current is None:
            return None
        if self.hashes[key] == key_hash:
            return current
        self.hashes[key] = key_hash
        updated = replace(
            current,
            credential_key_prefix=key_prefix,
            credential_status="active",
            updated_at=changed_at,
        )
        self.sites[key] = updated
        self.audit_events.append("support_site.key_rotated")
        return updated


def principal(roles: frozenset[str] = frozenset({"tenant_owner"})) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        subject_id="owner-1",
        tenant_id="tenant-a",
        roles=roles,
        scopes=scopes_for_roles(roles),
        authentication_method="admin_session",
        authenticated_at=datetime.now(UTC),
        correlation_id="correlation-1",
    )


async def test_site_create_is_idempotent_and_tenant_scoped() -> None:
    port = InMemorySiteAdministrationPort()
    service = SiteAdministrationService(port)
    command = CreateManagedSiteCommand(
        principal=principal(),
        site_id="shop-main",
        name="Main Shop",
        base_url="https://shop.example.com/",
        site_key="a" * 32,
        correlation_id="create-site",
    )

    first = await service.create_site(command)
    second = await service.create_site(command)
    listed = await service.list_sites(ListManagedSitesQuery(principal()))

    assert first == second
    assert first.base_url == "https://shop.example.com"
    assert listed.items == (first,)
    assert port.hashes[("tenant-a", "shop-main")] == hash_site_key("a" * 32)
    assert port.audit_events == ["support_site.created"]


async def test_site_create_rejects_conflicting_retry() -> None:
    port = InMemorySiteAdministrationPort()
    service = SiteAdministrationService(port)
    await service.create_site(
        CreateManagedSiteCommand(
            principal(), "shop", "Shop", "https://shop.example.com", "a" * 32, "create"
        )
    )

    with pytest.raises(SiteAdministrationConflictError):
        await service.create_site(
            CreateManagedSiteCommand(
                principal(), "shop", "Other", "https://shop.example.com", "a" * 32, "retry"
            )
        )


@pytest.mark.parametrize(
    ("site_id", "base_url", "site_key"),
    [("Invalid ID", "", "a" * 32), ("shop", "ftp://example.com", "a" * 32), ("shop", "", "short")],
)
async def test_site_create_validates_untrusted_fields(
    site_id: str, base_url: str, site_key: str
) -> None:
    service = SiteAdministrationService(InMemorySiteAdministrationPort())

    with pytest.raises(ValueError):
        await service.create_site(
            CreateManagedSiteCommand(principal(), site_id, "Shop", base_url, site_key, "create")
        )


async def test_only_tenant_owner_can_manage_sites() -> None:
    service = SiteAdministrationService(InMemorySiteAdministrationPort())

    with pytest.raises(PermissionError, match="site management"):
        await service.list_sites(ListManagedSitesQuery(principal(frozenset({"support_manager"}))))


async def test_update_and_key_rotation_are_retry_safe() -> None:
    port = InMemorySiteAdministrationPort()
    service = SiteAdministrationService(port)
    await service.create_site(
        CreateManagedSiteCommand(
            principal(), "shop", "Shop", "https://shop.example.com", "a" * 32, "create"
        )
    )
    update = UpdateManagedSiteCommand(
        principal(), "shop", "Shop Disabled", "https://shop.example.com", "disabled", "update"
    )
    rotate = RotateManagedSiteKeyCommand(principal(), "shop", "b" * 32, "rotate")

    first_update = await service.update_site(update)
    second_update = await service.update_site(update)
    first_rotate = await service.rotate_key(rotate)
    second_rotate = await service.rotate_key(rotate)

    assert first_update == second_update
    assert first_rotate == second_rotate
    assert first_rotate.credential_key_prefix == "bbbbbbbb"
    assert port.audit_events == [
        "support_site.created",
        "support_site.updated",
        "support_site.key_rotated",
    ]
