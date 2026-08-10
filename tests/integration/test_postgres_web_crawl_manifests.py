import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete

from app.application.tenant_context import tenant_scope
from app.domain.models import (
    WebCrawlManifest,
    WebCrawlManifestItem,
    WebCrawlManifestStatus,
    WebCrawlPageState,
)
from app.integrations.postgres import DatabaseSessionManager, PostgreSQLWebCrawlManifestStore
from app.integrations.postgres.models import (
    SupportSiteModel,
    TenantModel,
    WebCrawlManifestItemModel,
    WebCrawlManifestModel,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION_TESTS") != "1",
        reason="set RUN_INTEGRATION_TESTS=1 with PostgreSQL running",
    ),
]

DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/customer_agent",
)


async def test_manifest_identity_and_visibility_are_tenant_scoped() -> None:
    suffix = uuid4().hex
    tenant_ids = (f"manifest-a-{suffix}", f"manifest-b-{suffix}")
    site_id = "shared-site"
    manifest_id = "shared-manifest"
    now = datetime.now(UTC)
    manager = DatabaseSessionManager(DATABASE_URL)
    store = PostgreSQLWebCrawlManifestStore(manager.session_factory)
    try:
        async with manager.session_factory.begin() as session:
            session.add_all(
                TenantModel(
                    tenant_id=tenant_id,
                    name=f"Manifest Workspace {index + 1}",
                    status="active",
                    created_at=now,
                    updated_at=now,
                )
                for index, tenant_id in enumerate(tenant_ids)
            )
        for tenant_id in tenant_ids:
            with tenant_scope(tenant_id):
                async with manager.session_factory.begin() as session:
                    session.add(
                        SupportSiteModel(
                            tenant_id=tenant_id,
                            site_id=site_id,
                            public_widget_id=f"site_pub_{uuid4().hex}",
                            name=f"Site {tenant_id}",
                            base_url="https://shop.example.com",
                            allowed_origins=["https://shop.example.com"],
                            widget_daily_message_limit=100,
                            primary_language="en",
                            status="active",
                            created_at=now,
                            updated_at=now,
                        )
                    )
                saved = await store.save(_manifest(tenant_id, site_id, manifest_id, now))
                assert saved.version > 0

        for tenant_id in tenant_ids:
            with tenant_scope(tenant_id):
                visible = await store.get(
                    tenant_id=tenant_id,
                    site_id=site_id,
                    manifest_id=manifest_id,
                )
                assert visible is not None
                assert visible.tenant_id == tenant_id
                assert visible.items[0].url.endswith(f"/{tenant_id}.html")
                assert visible.items[0].document_id == "document-1"
                assert visible.items[0].version_id == "version-1"
                assert visible.items[0].etag == '"etag-v1"'
                assert visible.items[0].response_last_modified == "Sun, 27 Jul 2026 00:00:00 GMT"
                assert visible.items[0].product_key == "SKU-100"
                assert visible.items[0].validated_at == now
                metadata = await store.get_metadata(
                    tenant_id=tenant_id,
                    site_id=site_id,
                    manifest_id=manifest_id,
                )
                assert metadata is not None
                assert metadata.items == ()
                assert metadata.url_count == 1
                assert metadata.content_kind_counts == {"general": 1}

                url = visible.items[0].url
                await store.replace_page_states(
                    tenant_id=tenant_id,
                    site_id=site_id,
                    states=(
                        WebCrawlPageState(
                            tenant_id=tenant_id,
                            site_id=site_id,
                            url=url,
                            document_id="document-1",
                            version_id="version-1",
                            canonical_url=url,
                            final_url=url,
                            artifact_status="published",
                            validated_at=now,
                        ),
                    ),
                )
                assert tuple(
                    state.url
                    for state in await store.list_page_states(
                        tenant_id=tenant_id,
                        site_id=site_id,
                    )
                ) == (url,)

        with tenant_scope(tenant_ids[0]):
            await store.replace_page_states(
                tenant_id=tenant_ids[0],
                site_id=site_id,
                states=(),
            )
            assert (
                await store.list_page_states(
                    tenant_id=tenant_ids[0],
                    site_id=site_id,
                )
                == ()
            )
        with tenant_scope(tenant_ids[1]):
            assert (
                len(
                    await store.list_page_states(
                        tenant_id=tenant_ids[1],
                        site_id=site_id,
                    )
                )
                == 1
            )
    finally:
        for tenant_id in tenant_ids:
            with tenant_scope(tenant_id):
                async with manager.session_factory.begin() as session:
                    await session.execute(
                        delete(WebCrawlManifestItemModel).where(
                            WebCrawlManifestItemModel.tenant_id == tenant_id
                        )
                    )
                    await session.execute(
                        delete(WebCrawlManifestModel).where(
                            WebCrawlManifestModel.tenant_id == tenant_id
                        )
                    )
                    await session.execute(
                        delete(SupportSiteModel).where(SupportSiteModel.tenant_id == tenant_id)
                    )
        async with manager.session_factory.begin() as session:
            await session.execute(delete(TenantModel).where(TenantModel.tenant_id.in_(tenant_ids)))
        await manager.dispose()


def _manifest(
    tenant_id: str,
    site_id: str,
    manifest_id: str,
    created_at: datetime,
) -> WebCrawlManifest:
    return WebCrawlManifest(
        tenant_id=tenant_id,
        site_id=site_id,
        manifest_id=manifest_id,
        base_url="https://shop.example.com",
        root_sitemap_url="https://shop.example.com/sitemap.xml",
        primary_language="en",
        translation_provider="gtranslate",
        status=WebCrawlManifestStatus.READY,
        fingerprint="a" * 64,
        primary_sitemap_urls=("https://shop.example.com/sitemap-products.xml",),
        translated_locales=("de",),
        excluded_sitemap_count=1,
        excluded_url_count=0,
        blocking_reasons=(),
        created_by="admin-1",
        created_at=created_at,
        items=(
            WebCrawlManifestItem(
                url=f"https://shop.example.com/{tenant_id}.html",
                source_sitemap_url="https://shop.example.com/sitemap-products.xml",
                document_id="document-1",
                version_id="version-1",
                canonical_url=f"https://shop.example.com/{tenant_id}.html",
                final_url=f"https://shop.example.com/{tenant_id}.html",
                etag='"etag-v1"',
                response_last_modified="Sun, 27 Jul 2026 00:00:00 GMT",
                product_key="SKU-100",
                validated_at=created_at,
            ),
        ),
    )
