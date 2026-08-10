import pytest

from app.integrations.auth import StaticWidgetSiteAuthenticationAdapter


async def test_widget_site_key_maps_to_trusted_tenant() -> None:
    adapter = StaticWidgetSiteAuthenticationAdapter({"site-secret-key": "tenant-a"})

    principal = await adapter.authenticate_site(
        site_key="site-secret-key",
        correlation_id="correlation-1",
    )

    assert principal.tenant_id == "tenant-a"
    assert principal.site_id == "default-site"
    assert principal.is_anonymous
    assert principal.authentication_method == "widget_site_key"
    assert principal.scopes == frozenset({"knowledge:read"})


async def test_widget_site_key_rejects_unknown_key() -> None:
    adapter = StaticWidgetSiteAuthenticationAdapter({"site-secret-key": "tenant-a"})

    with pytest.raises(PermissionError, match="invalid widget"):
        await adapter.authenticate_site(
            site_key="wrong-site-secret",
            correlation_id="correlation-1",
        )


async def test_widget_site_key_supports_explicit_trusted_site_mapping() -> None:
    adapter = StaticWidgetSiteAuthenticationAdapter(
        {"site-secret-key": {"tenant_id": "tenant-a", "site_id": "site-a"}}
    )

    principal = await adapter.authenticate_site(
        site_key="site-secret-key",
        correlation_id="correlation-1",
    )

    assert principal.tenant_id == "tenant-a"
    assert principal.site_id == "site-a"
