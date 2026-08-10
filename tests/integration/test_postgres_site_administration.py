import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from app.application.dto import (
    CompleteSiteVerificationCommand,
    CreateManagedSiteCommand,
    IssueSiteVerificationChallengeCommand,
    ListManagedSitesQuery,
    RotateManagedSiteKeyCommand,
    UpdateManagedSiteCommand,
)
from app.application.services import SiteAdministrationService
from app.domain.models import AuthenticatedPrincipal
from app.domain.rules.rbac import scopes_for_roles
from app.integrations.auth import PostgreSQLWidgetSiteAuthenticationAdapter
from app.integrations.postgres.models import (
    AuditEventModel,
    SiteUsageDailyModel,
    SupportSiteModel,
    TenantModel,
    WidgetMessageAdmissionModel,
    WidgetSiteCredentialModel,
    WidgetVisitorSessionModel,
)
from app.integrations.postgres.public_widget import PostgreSQLPublicWidgetAccessAdapter
from app.integrations.postgres.session import DatabaseSessionManager
from app.integrations.postgres.site_admin import PostgreSQLSiteAdministrationAdapter

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


def owner_principal(tenant_id: str) -> AuthenticatedPrincipal:
    roles = frozenset({"tenant_owner"})
    return AuthenticatedPrincipal(
        subject_id="owner-1",
        tenant_id=tenant_id,
        roles=roles,
        scopes=scopes_for_roles(roles),
        authentication_method="admin_session",
        authenticated_at=datetime.now(UTC),
        correlation_id="correlation-1",
    )


class ScriptVerificationProbe:
    def __init__(self) -> None:
        self.proof = ""

    async def resolve_dns_txt(self, *, base_url: str) -> list[str]:
        del base_url
        return []

    async def fetch_script_proof(self, *, base_url: str) -> str:
        del base_url
        return self.proof


@pytest.mark.asyncio
async def test_site_credentials_rotate_disable_and_authenticate_transactionally() -> None:
    suffix = uuid4().hex
    tenant_id = f"tenant-site-{suffix}"
    site_id = f"shop-{suffix[:12]}"
    first_key = "first-site-key-" + suffix
    second_key = "second-site-key-" + suffix
    manager = DatabaseSessionManager(DATABASE_URL)
    verification_probe = ScriptVerificationProbe()
    service = SiteAdministrationService(
        PostgreSQLSiteAdministrationAdapter(manager.session_factory),
        default_daily_message_limit=1,
        verification_token_secret="integration-verification-secret",
        verification_probe=verification_probe,
    )
    authentication = PostgreSQLWidgetSiteAuthenticationAdapter(manager.session_factory)
    public_access = PostgreSQLPublicWidgetAccessAdapter(manager.session_factory)
    principal = owner_principal(tenant_id)

    try:
        now = datetime.now(UTC)
        async with manager.session_factory.begin() as session:
            session.add(
                TenantModel(
                    tenant_id=tenant_id,
                    name="Site Administration Integration",
                    status="active",
                    created_at=now,
                    updated_at=now,
                )
            )
        command = CreateManagedSiteCommand(
            principal=principal,
            site_id=site_id,
            name="Integration Shop",
            base_url="https://shop.example.com",
            site_key=first_key,
            correlation_id="site-create",
        )
        created = await service.create_site(command)
        repeated = await service.create_site(command)
        authenticated = await authentication.authenticate_site(
            site_key=first_key, correlation_id="widget-first"
        )

        assert repeated == created
        assert authenticated.tenant_id == tenant_id
        assert authenticated.site_id == site_id
        public_site = await public_access.get_public_site(public_widget_id=created.public_widget_id)
        assert public_site is None

        challenge = await service.issue_verification_challenge(
            IssueSiteVerificationChallengeCommand(
                principal, site_id, "script", "site-verification-challenge"
            )
        )
        verification_probe.proof = challenge.script_value
        verified = await service.verify_site(
            CompleteSiteVerificationCommand(principal, site_id, "script", "site-verified")
        )
        assert verified.verification_status == "verified"

        public_site = await public_access.get_public_site(public_widget_id=created.public_widget_id)
        assert public_site is not None
        assert public_site.allowed_origins == ("https://shop.example.com",)
        assert await public_access.admit_message(
            tenant_id=tenant_id,
            site_id=site_id,
            request_id="public-request-1",
            occurred_at=datetime.now(UTC),
        )
        assert await public_access.admit_message(
            tenant_id=tenant_id,
            site_id=site_id,
            request_id="public-request-1",
            occurred_at=datetime.now(UTC),
        )
        assert not await public_access.admit_message(
            tenant_id=tenant_id,
            site_id=site_id,
            request_id="public-request-2",
            occurred_at=datetime.now(UTC),
        )

        rotated = await service.rotate_key(
            RotateManagedSiteKeyCommand(principal, site_id, second_key, "site-rotate")
        )
        repeated_rotation = await service.rotate_key(
            RotateManagedSiteKeyCommand(principal, site_id, second_key, "site-rotate-retry")
        )
        assert repeated_rotation == rotated
        with pytest.raises(PermissionError):
            await authentication.authenticate_site(
                site_key=first_key, correlation_id="widget-old-key"
            )
        new_authentication = await authentication.authenticate_site(
            site_key=second_key, correlation_id="widget-new-key"
        )
        assert new_authentication.site_id == site_id

        disabled = await service.update_site(
            UpdateManagedSiteCommand(
                principal,
                site_id,
                "Integration Shop",
                "https://shop.example.com",
                "disabled",
                "site-disable",
            )
        )
        assert disabled.status == "disabled"
        with pytest.raises(PermissionError):
            await authentication.authenticate_site(
                site_key=second_key, correlation_id="widget-disabled"
            )

        listed = await service.list_sites(ListManagedSitesQuery(principal))
        assert [item.site_id for item in listed.items] == [site_id]

        async with manager.session_factory() as session:
            credential = await session.scalar(
                select(WidgetSiteCredentialModel).where(
                    WidgetSiteCredentialModel.tenant_id == tenant_id
                )
            )
            audit_count = await session.scalar(
                select(func.count())
                .select_from(AuditEventModel)
                .where(AuditEventModel.tenant_id == tenant_id)
            )
            audit_types = list(
                await session.scalars(
                    select(AuditEventModel.event_type).where(AuditEventModel.tenant_id == tenant_id)
                )
            )
        assert credential is not None
        assert credential.key_hash not in {first_key, second_key}
        assert audit_count == 6
        assert sorted(audit_types) == [
            "public_widget.volume_anomaly",
            "support_site.created",
            "support_site.key_rotated",
            "support_site.updated",
            "support_site.verification_challenge_issued",
            "support_site.verified",
        ]
    finally:
        async with manager.session_factory.begin() as session:
            await session.execute(
                delete(WidgetVisitorSessionModel).where(
                    WidgetVisitorSessionModel.tenant_id == tenant_id
                )
            )
            await session.execute(
                delete(SiteUsageDailyModel).where(SiteUsageDailyModel.tenant_id == tenant_id)
            )
            await session.execute(
                delete(WidgetMessageAdmissionModel).where(
                    WidgetMessageAdmissionModel.tenant_id == tenant_id
                )
            )
            await session.execute(
                delete(AuditEventModel).where(AuditEventModel.tenant_id == tenant_id)
            )
            await session.execute(
                delete(WidgetSiteCredentialModel).where(
                    WidgetSiteCredentialModel.tenant_id == tenant_id
                )
            )
            await session.execute(
                delete(SupportSiteModel).where(SupportSiteModel.tenant_id == tenant_id)
            )
            await session.execute(delete(TenantModel).where(TenantModel.tenant_id == tenant_id))
        await manager.dispose()
