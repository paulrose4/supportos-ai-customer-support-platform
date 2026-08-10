from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api.routes.site_admin import router
from app.application.dto import (
    GetSiteWebSourceConfigQuery,
    UpdateSiteWebSourceConfigCommand,
)
from app.application.services import (
    SiteWebSourceConfigConflictError,
    SiteWebSourceConfigService,
)
from app.domain.models import (
    AuthenticatedPrincipal,
    SiteWebDiscoveryMode,
    SiteWebSourceConfig,
    SiteWebSourceValidationStatus,
)
from app.domain.ports.site_web_source import SiteWebSourceConfigVersionConflictError


class InMemorySiteWebSourceStore:
    def __init__(self) -> None:
        self.sites = {
            ("tenant-a", "shop"): "https://shop.example.com",
            ("tenant-a", "sitemap-cdn"): "https://maps.example-cdn.com/content",
            ("tenant-b", "shop"): "https://other.example.com",
            ("tenant-b", "foreign-cdn"): "https://foreign.example-cdn.com",
        }
        self.active_verified_sites = {
            ("tenant-a", "shop"),
            ("tenant-a", "sitemap-cdn"),
            ("tenant-b", "shop"),
            ("tenant-b", "foreign-cdn"),
        }
        self.configs: dict[tuple[str, str], SiteWebSourceConfig] = {}
        self.write_count = 0

    async def get_site_base_url(self, *, tenant_id: str, site_id: str) -> str | None:
        return self.sites.get((tenant_id, site_id))

    async def list_active_verified_site_base_urls(
        self,
        *,
        tenant_id: str,
    ) -> tuple[str, ...]:
        return tuple(
            base_url
            for key, base_url in self.sites.items()
            if key[0] == tenant_id and key in self.active_verified_sites
        )

    async def get_config(
        self,
        *,
        tenant_id: str,
        site_id: str,
    ) -> SiteWebSourceConfig | None:
        return self.configs.get((tenant_id, site_id))

    async def put_config(
        self,
        *,
        tenant_id: str,
        site_id: str,
        expected_base_url: str,
        expected_config_version: int,
        discovery_mode: SiteWebDiscoveryMode,
        explicit_sitemap_urls: tuple[str, ...],
        actor_subject_id: str,
        correlation_id: str,
        changed_at: datetime,
    ) -> SiteWebSourceConfig | None:
        del correlation_id
        key = (tenant_id, site_id)
        if self.sites.get(key) != expected_base_url:
            return None
        current = self.configs.get(key)
        if (
            current is not None
            and current.discovery_mode is discovery_mode
            and current.explicit_sitemap_urls == explicit_sitemap_urls
        ):
            return current
        current_version = 0 if current is None else current.config_version
        if current_version != expected_config_version:
            raise SiteWebSourceConfigVersionConflictError
        config = SiteWebSourceConfig(
            tenant_id=tenant_id,
            site_id=site_id,
            discovery_mode=discovery_mode,
            explicit_sitemap_urls=explicit_sitemap_urls,
            config_version=1 if current is None else current.config_version + 1,
            validation_status=SiteWebSourceValidationStatus.UNVALIDATED,
            validated_at=None,
            updated_by=actor_subject_id,
            updated_at=changed_at,
        )
        self.configs[key] = config
        self.write_count += 1
        return config

    async def mark_validation_status(
        self,
        *,
        tenant_id: str,
        site_id: str,
        expected_config_version: int,
        validation_status: SiteWebSourceValidationStatus,
        validated_at: datetime | None,
        actor_subject_id: str,
        correlation_id: str,
        changed_at: datetime,
    ) -> SiteWebSourceConfig | None:
        del actor_subject_id, correlation_id, changed_at
        key = (tenant_id, site_id)
        current = self.configs.get(key)
        if current is None or current.config_version != expected_config_version:
            return None
        updated = replace(
            current,
            validation_status=validation_status,
            validated_at=validated_at,
        )
        self.configs[key] = updated
        return updated


def principal(*scopes: str, tenant_id: str = "tenant-a") -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        subject_id="owner-1",
        tenant_id=tenant_id,
        roles=frozenset({"tenant_owner"}),
        scopes=frozenset(scopes),
        authentication_method="admin_session",
        authenticated_at=datetime.now(UTC),
        correlation_id="correlation-1",
    )


async def test_get_returns_hybrid_default_without_creating_a_config() -> None:
    store = InMemorySiteWebSourceStore()
    service = SiteWebSourceConfigService(store)

    result = await service.get_config(GetSiteWebSourceConfigQuery(principal("sites:read"), "shop"))

    assert result.config.discovery_mode is SiteWebDiscoveryMode.HYBRID
    assert result.config.explicit_sitemap_urls == ()
    assert result.config.config_version == 0
    assert result.config.validation_status is SiteWebSourceValidationStatus.UNVALIDATED
    assert store.configs == {}


async def test_update_normalizes_urls_and_unchanged_retry_keeps_version() -> None:
    store = InMemorySiteWebSourceStore()
    service = SiteWebSourceConfigService(store)
    command = UpdateSiteWebSourceConfigCommand(
        principal=principal("sites:manage"),
        site_id="shop",
        discovery_mode=" HYBRID ",
        explicit_sitemap_urls=(
            " https://SHOP.example.com:443/sitemap_index.xml?lang=en ",
            "https://shop.example.com/sitemap_index.xml?lang=en",
            "https://shop.example.com/catalog-map",
        ),
        correlation_id="update-web-source",
    )

    first = await service.update_config(command)
    second = await service.update_config(command)

    assert first.config == second.config
    assert first.config.config_version == 1
    assert first.config.explicit_sitemap_urls == (
        "https://shop.example.com/sitemap_index.xml?lang=en",
        "https://shop.example.com/catalog-map",
    )
    assert store.write_count == 1


async def test_update_allows_sitemap_origin_verified_by_another_site_in_same_tenant() -> None:
    store = InMemorySiteWebSourceStore()
    service = SiteWebSourceConfigService(store)

    result = await service.update_config(
        UpdateSiteWebSourceConfigCommand(
            principal=principal("sites:manage"),
            site_id="shop",
            discovery_mode="manual",
            explicit_sitemap_urls=("https://maps.example-cdn.com/catalog/map?locale=en",),
            correlation_id="update-web-source",
        )
    )

    assert result.config.explicit_sitemap_urls == (
        "https://maps.example-cdn.com/catalog/map?locale=en",
    )
    assert result.allowed_sitemap_origins == (
        "https://maps.example-cdn.com",
        "https://shop.example.com",
    )


async def test_update_rejects_cross_tenant_or_unverified_sitemap_origin() -> None:
    store = InMemorySiteWebSourceStore()
    store.sites[("tenant-a", "pending-cdn")] = "https://pending.example-cdn.com"
    service = SiteWebSourceConfigService(store)

    for url in (
        "https://foreign.example-cdn.com/sitemap.xml",
        "https://pending.example-cdn.com/sitemap.xml",
    ):
        with pytest.raises(ValueError, match="active, verified site in the same tenant"):
            await service.update_config(
                UpdateSiteWebSourceConfigCommand(
                    principal=principal("sites:manage"),
                    site_id="shop",
                    discovery_mode="manual",
                    explicit_sitemap_urls=(url,),
                    correlation_id="update-web-source",
                )
            )


async def test_get_removes_origin_when_verification_is_no_longer_current() -> None:
    store = InMemorySiteWebSourceStore()
    service = SiteWebSourceConfigService(store)
    await service.update_config(
        UpdateSiteWebSourceConfigCommand(
            principal=principal("sites:manage"),
            site_id="shop",
            discovery_mode="manual",
            explicit_sitemap_urls=("https://maps.example-cdn.com/sitemap.xml",),
            correlation_id="update-web-source",
        )
    )
    store.active_verified_sites.remove(("tenant-a", "sitemap-cdn"))

    result = await service.get_config(GetSiteWebSourceConfigQuery(principal("sites:read"), "shop"))

    assert result.config.explicit_sitemap_urls == ("https://maps.example-cdn.com/sitemap.xml",)
    assert result.allowed_sitemap_origins == ("https://shop.example.com",)


@pytest.mark.parametrize(
    "url",
    [
        "http://shop.example.com/sitemap.xml",
        "https://user:password@shop.example.com/sitemap.xml",
        "https://shop.example.com:8443/sitemap.xml",
        "https://cdn.example.com/sitemap.xml",
        "https://shop.example.com/sitemap.xml#section",
        "https://shop.example.com/site map.xml",
    ],
)
async def test_update_rejects_unsafe_or_cross_origin_urls(url: str) -> None:
    service = SiteWebSourceConfigService(InMemorySiteWebSourceStore())

    with pytest.raises(ValueError):
        await service.update_config(
            UpdateSiteWebSourceConfigCommand(
                principal("sites:manage"),
                "shop",
                "hybrid",
                (url,),
                "update-web-source",
            )
        )


async def test_manual_mode_requires_an_explicit_sitemap() -> None:
    service = SiteWebSourceConfigService(InMemorySiteWebSourceStore())

    with pytest.raises(ValueError, match="at least one"):
        await service.update_config(
            UpdateSiteWebSourceConfigCommand(
                principal("sites:manage"),
                "shop",
                "manual",
                (),
                "update-web-source",
            )
        )


async def test_permissions_and_tenant_scope_are_enforced() -> None:
    service = SiteWebSourceConfigService(InMemorySiteWebSourceStore())

    with pytest.raises(PermissionError):
        await service.get_config(GetSiteWebSourceConfigQuery(principal(), "shop"))
    with pytest.raises(PermissionError):
        await service.update_config(
            UpdateSiteWebSourceConfigCommand(
                principal("sites:read"),
                "shop",
                "auto",
                (),
                "update-web-source",
            )
        )
    with pytest.raises(LookupError):
        await service.get_config(
            GetSiteWebSourceConfigQuery(
                principal("sites:read", tenant_id="tenant-missing"),
                "shop",
            )
        )


async def test_completed_validation_requires_a_timestamp() -> None:
    store = InMemorySiteWebSourceStore()
    service = SiteWebSourceConfigService(store)
    await service.update_config(
        UpdateSiteWebSourceConfigCommand(
            principal("sites:manage"),
            "shop",
            "auto",
            (),
            "update-web-source",
        )
    )

    with pytest.raises(ValueError, match="validated_at"):
        await service.mark_validation_status(
            principal=principal("sites:manage"),
            site_id="shop",
            expected_config_version=1,
            validation_status=SiteWebSourceValidationStatus.VALID,
            validated_at=None,
        )


async def test_stale_preflight_cannot_validate_a_newer_source_config() -> None:
    store = InMemorySiteWebSourceStore()
    service = SiteWebSourceConfigService(store)
    first = await service.update_config(
        UpdateSiteWebSourceConfigCommand(
            principal("sites:manage"),
            "shop",
            "manual",
            ("https://shop.example.com/first-map.xml",),
            "first-update",
        )
    )
    second = await service.update_config(
        UpdateSiteWebSourceConfigCommand(
            principal("sites:manage"),
            "shop",
            "manual",
            ("https://shop.example.com/second-map.xml",),
            "second-update",
            expected_config_version=first.config.config_version,
        )
    )

    stale_result = await service.mark_validation_status(
        principal=principal("sites:manage"),
        site_id="shop",
        expected_config_version=first.config.config_version,
        validation_status=SiteWebSourceValidationStatus.VALID,
        validated_at=datetime.now(UTC),
    )

    assert stale_result is None
    assert second.config.config_version == 2
    assert store.configs[("tenant-a", "shop")].validation_status is (
        SiteWebSourceValidationStatus.UNVALIDATED
    )


async def test_update_rejects_a_stale_configuration_version() -> None:
    store = InMemorySiteWebSourceStore()
    service = SiteWebSourceConfigService(store)
    first = await service.update_config(
        UpdateSiteWebSourceConfigCommand(
            principal("sites:manage"),
            "shop",
            "hybrid",
            ("https://shop.example.com/first-map.xml",),
            "first-update",
        )
    )

    with pytest.raises(SiteWebSourceConfigConflictError, match="reload"):
        await service.update_config(
            UpdateSiteWebSourceConfigCommand(
                principal("sites:manage"),
                "shop",
                "manual",
                ("https://shop.example.com/stale-map.xml",),
                "stale-update",
                expected_config_version=0,
            )
        )

    assert store.configs[("tenant-a", "shop")] == first.config


class FakeAuthentication:
    def __init__(self, authenticated_principal: AuthenticatedPrincipal) -> None:
        self.principal = authenticated_principal

    async def authenticate(self, correlation_id: str) -> AuthenticatedPrincipal:
        del correlation_id
        return self.principal


async def _correlation_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
    request.state.correlation_id = "correlation-test"
    return await call_next(request)


def _client(
    store: InMemorySiteWebSourceStore,
    *,
    authenticated_principal: AuthenticatedPrincipal,
) -> TestClient:
    application = FastAPI()
    application.middleware("http")(_correlation_middleware)
    application.include_router(router)
    application.state.container = SimpleNamespace(
        authentication=FakeAuthentication(authenticated_principal),
        admin_session_service=None,
        site_web_source_config_service=SiteWebSourceConfigService(store),
    )
    return TestClient(application)


def test_web_source_api_gets_default_and_updates_config() -> None:
    store = InMemorySiteWebSourceStore()
    with _client(store, authenticated_principal=principal("sites:read", "sites:manage")) as client:
        default_response = client.get("/v1/admin/site-management/shop/web-source")
        update_response = client.put(
            "/v1/admin/site-management/shop/web-source",
            json={
                "discovery_mode": "manual",
                "explicit_sitemap_urls": [
                    "https://shop.example.com/custom_sitemap.xml?catalog=main"
                ],
                "expected_config_version": 0,
            },
        )

    assert default_response.status_code == 200
    assert default_response.json() == {
        "site_id": "shop",
        "discovery_mode": "hybrid",
        "explicit_sitemap_urls": [],
        "allowed_sitemap_origins": [
            "https://maps.example-cdn.com",
            "https://shop.example.com",
        ],
        "config_version": 0,
        "validation_status": "unvalidated",
        "validated_at": None,
        "updated_by": None,
        "updated_at": None,
    }
    assert update_response.status_code == 200
    assert update_response.json()["config_version"] == 1
    assert update_response.json()["explicit_sitemap_urls"] == [
        "https://shop.example.com/custom_sitemap.xml?catalog=main"
    ]


def test_web_source_api_maps_missing_site_and_invalid_payload() -> None:
    store = InMemorySiteWebSourceStore()
    with _client(store, authenticated_principal=principal("sites:read", "sites:manage")) as client:
        missing = client.get("/v1/admin/site-management/missing/web-source")
        invalid = client.put(
            "/v1/admin/site-management/shop/web-source",
            json={
                "discovery_mode": "manual",
                "explicit_sitemap_urls": ["https://other.example.com/sitemap.xml"],
                "expected_config_version": 0,
            },
        )

    assert missing.status_code == 404
    assert invalid.status_code == 422


def test_web_source_api_returns_conflict_for_a_stale_update() -> None:
    store = InMemorySiteWebSourceStore()
    with _client(store, authenticated_principal=principal("sites:read", "sites:manage")) as client:
        first = client.put(
            "/v1/admin/site-management/shop/web-source",
            json={
                "discovery_mode": "hybrid",
                "explicit_sitemap_urls": ["https://shop.example.com/first.xml"],
                "expected_config_version": 0,
            },
        )
        stale = client.put(
            "/v1/admin/site-management/shop/web-source",
            json={
                "discovery_mode": "manual",
                "explicit_sitemap_urls": ["https://shop.example.com/stale.xml"],
                "expected_config_version": 0,
            },
        )

    assert first.status_code == 200
    assert stale.status_code == 409
