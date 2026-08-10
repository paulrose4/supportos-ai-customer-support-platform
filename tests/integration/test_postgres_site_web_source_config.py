import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from app.application.dto import UpdateSiteWebSourceConfigCommand
from app.application.services import SiteWebSourceConfigService
from app.application.tenant_context import tenant_scope
from app.domain.models import (
    AuthenticatedPrincipal,
    SiteWebSourceValidationStatus,
)
from app.integrations.postgres.models import (
    AuditEventModel,
    SiteWebSourceConfigModel,
    SupportSiteModel,
    TenantModel,
)
from app.integrations.postgres.session import DatabaseSessionManager
from app.integrations.postgres.site_web_source import PostgreSQLSiteWebSourceConfigStore

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/customer_agent",
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION_TESTS") != "1",
        reason="set RUN_INTEGRATION_TESTS=1 with PostgreSQL running",
    ),
]


def _principal(tenant_id: str) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        subject_id="owner-1",
        tenant_id=tenant_id,
        roles=frozenset({"tenant_owner"}),
        scopes=frozenset({"sites:read", "sites:manage"}),
        authentication_method="admin_session",
        authenticated_at=datetime.now(UTC),
        correlation_id="correlation-1",
    )


@pytest.mark.asyncio
async def test_postgres_site_web_source_config_is_idempotent_and_audited() -> None:
    suffix = uuid4().hex
    tenant_id = f"tenant-web-source-{suffix}"
    site_id = f"shop-{suffix[:12]}"
    now = datetime.now(UTC)
    manager = DatabaseSessionManager(DATABASE_URL)
    store = PostgreSQLSiteWebSourceConfigStore(manager.session_factory)
    service = SiteWebSourceConfigService(store)
    principal = _principal(tenant_id)

    try:
        with tenant_scope(tenant_id):
            async with manager.session_factory.begin() as session:
                session.add_all(
                    (
                        TenantModel(
                            tenant_id=tenant_id,
                            name="Web Source Workspace",
                            status="active",
                            created_at=now,
                            updated_at=now,
                        ),
                        SupportSiteModel(
                            tenant_id=tenant_id,
                            site_id=site_id,
                            public_widget_id=f"site_pub_{suffix}",
                            name="Web Source Shop",
                            base_url="https://shop.example.com",
                            allowed_origins=["https://shop.example.com"],
                            widget_daily_message_limit=500,
                            primary_language="en",
                            status="active",
                            verification_status="verified",
                            created_at=now,
                            updated_at=now,
                        ),
                    )
                )

            command = UpdateSiteWebSourceConfigCommand(
                principal=principal,
                site_id=site_id,
                discovery_mode="manual",
                explicit_sitemap_urls=("https://shop.example.com/sitemap_index.xml?catalog=main",),
                expected_config_version=0,
                correlation_id="web-source-update",
            )
            first = await service.update_config(command)
            repeated = await service.update_config(command)
            validated_at = datetime.now(UTC)
            validated = await service.mark_validation_status(
                principal=principal,
                site_id=site_id,
                expected_config_version=first.config.config_version,
                validation_status=SiteWebSourceValidationStatus.VALID,
                validated_at=validated_at,
            )

            async with manager.session_factory() as session:
                audit_count = await session.scalar(
                    select(func.count())
                    .select_from(AuditEventModel)
                    .where(
                        AuditEventModel.tenant_id == tenant_id,
                        AuditEventModel.event_type == "site_web_source_config.updated",
                    )
                )
                validation_audit_count = await session.scalar(
                    select(func.count())
                    .select_from(AuditEventModel)
                    .where(
                        AuditEventModel.tenant_id == tenant_id,
                        AuditEventModel.event_type == "site_web_source_config.validation_updated",
                    )
                )
                persisted = await session.scalar(
                    select(SiteWebSourceConfigModel).where(
                        SiteWebSourceConfigModel.tenant_id == tenant_id,
                        SiteWebSourceConfigModel.site_id == site_id,
                    )
                )

        assert first.config == repeated.config
        assert first.config.config_version == 1
        assert audit_count == 1
        assert validation_audit_count == 1
        assert validated is not None
        assert validated.validation_status is SiteWebSourceValidationStatus.VALID
        assert persisted is not None
        assert persisted.validated_at == validated_at
    finally:
        with tenant_scope(tenant_id):
            async with manager.session_factory.begin() as session:
                await session.execute(
                    delete(AuditEventModel).where(AuditEventModel.tenant_id == tenant_id)
                )
                await session.execute(
                    delete(SiteWebSourceConfigModel).where(
                        SiteWebSourceConfigModel.tenant_id == tenant_id
                    )
                )
                await session.execute(
                    delete(SupportSiteModel).where(SupportSiteModel.tenant_id == tenant_id)
                )
                await session.execute(delete(TenantModel).where(TenantModel.tenant_id == tenant_id))
        await manager.dispose()


@pytest.mark.asyncio
async def test_postgres_verified_sitemap_origins_are_current_and_tenant_scoped() -> None:
    suffix = uuid4().hex
    tenant_a = f"tenant-web-source-a-{suffix}"
    tenant_b = f"tenant-web-source-b-{suffix}"
    site_id = f"shop-{suffix[:12]}"
    now = datetime.now(UTC)
    manager = DatabaseSessionManager(DATABASE_URL)
    store = PostgreSQLSiteWebSourceConfigStore(manager.session_factory)
    service = SiteWebSourceConfigService(store)

    def site(
        *,
        tenant_id: str,
        site_id: str,
        base_url: str,
        status: str = "active",
        verification_status: str = "verified",
    ) -> SupportSiteModel:
        return SupportSiteModel(
            tenant_id=tenant_id,
            site_id=site_id,
            public_widget_id=f"site_pub_{suffix[:16]}_{site_id}",
            name=site_id,
            base_url=base_url,
            allowed_origins=[base_url],
            widget_daily_message_limit=500,
            primary_language="en",
            status=status,
            verification_status=verification_status,
            created_at=now,
            updated_at=now,
        )

    try:
        async with manager.session_factory.begin() as session:
            session.add_all(
                (
                    TenantModel(
                        tenant_id=tenant_a,
                        name="Verified Sitemap Workspace A",
                        status="active",
                        created_at=now,
                        updated_at=now,
                    ),
                    TenantModel(
                        tenant_id=tenant_b,
                        name="Verified Sitemap Workspace B",
                        status="active",
                        created_at=now,
                        updated_at=now,
                    ),
                )
            )
        with tenant_scope(tenant_a):
            async with manager.session_factory.begin() as session:
                session.add_all(
                    (
                        site(
                            tenant_id=tenant_a,
                            site_id=site_id,
                            base_url="https://shop-a.example.com",
                        ),
                        site(
                            tenant_id=tenant_a,
                            site_id=f"maps-{suffix[:12]}",
                            base_url="https://maps-a.example-cdn.com/content",
                        ),
                        site(
                            tenant_id=tenant_a,
                            site_id=f"pending-{suffix[:12]}",
                            base_url="https://pending-a.example-cdn.com",
                            verification_status="pending",
                        ),
                    )
                )
        with tenant_scope(tenant_b):
            async with manager.session_factory.begin() as session:
                session.add(
                    site(
                        tenant_id=tenant_b,
                        site_id=f"foreign-{suffix[:12]}",
                        base_url="https://maps-b.example-cdn.com",
                    )
                )

        principal = _principal(tenant_a)
        with tenant_scope(tenant_a):
            accepted = await service.update_config(
                UpdateSiteWebSourceConfigCommand(
                    principal=principal,
                    site_id=site_id,
                    discovery_mode="manual",
                    explicit_sitemap_urls=("https://maps-a.example-cdn.com/catalog/sitemap.xml",),
                    expected_config_version=0,
                    correlation_id="verified-cross-origin",
                )
            )
            for rejected_url in (
                "https://pending-a.example-cdn.com/sitemap.xml",
                "https://maps-b.example-cdn.com/sitemap.xml",
            ):
                with pytest.raises(ValueError, match="same tenant"):
                    await service.update_config(
                        UpdateSiteWebSourceConfigCommand(
                            principal=principal,
                            site_id=site_id,
                            discovery_mode="manual",
                            explicit_sitemap_urls=(rejected_url,),
                            expected_config_version=0,
                            correlation_id="rejected-cross-origin",
                        )
                    )

        assert accepted.allowed_sitemap_origins == (
            "https://maps-a.example-cdn.com",
            "https://shop-a.example.com",
        )
    finally:
        with tenant_scope(tenant_a):
            async with manager.session_factory.begin() as session:
                await session.execute(
                    delete(AuditEventModel).where(AuditEventModel.tenant_id == tenant_a)
                )
                await session.execute(
                    delete(SiteWebSourceConfigModel).where(
                        SiteWebSourceConfigModel.tenant_id == tenant_a
                    )
                )
                await session.execute(
                    delete(SupportSiteModel).where(SupportSiteModel.tenant_id == tenant_a)
                )
        with tenant_scope(tenant_b):
            async with manager.session_factory.begin() as session:
                await session.execute(
                    delete(SupportSiteModel).where(SupportSiteModel.tenant_id == tenant_b)
                )
        async with manager.session_factory.begin() as session:
            await session.execute(
                delete(TenantModel).where(TenantModel.tenant_id.in_((tenant_a, tenant_b)))
            )
        await manager.dispose()
