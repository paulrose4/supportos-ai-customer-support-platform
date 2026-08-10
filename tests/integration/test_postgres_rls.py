import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import DBAPIError

from app.application.dto import GetPlatformTenantQuery, ListPlatformSiteRecordsQuery
from app.application.services.platform_identity import PlatformIdentityService
from app.application.tenant_context import tenant_scope
from app.domain.models import AuthenticatedPrincipal
from app.integrations.postgres.models import (
    CustomerModel,
    EmailIdentityModel,
    IdentityUserModel,
    SupportSiteModel,
    TenantMembershipModel,
    TenantModel,
    TenantQuotaModel,
    TenantSubscriptionModel,
)
from app.integrations.postgres.session import DatabaseSessionManager
from app.integrations.postgres.workforce_identity import PostgreSQLWorkforceIdentityStore

MIGRATION_DATABASE_URL = os.getenv("MIGRATION_DATABASE_URL", "")
APP_DATABASE_URL = os.getenv("RLS_DATABASE_URL", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION_TESTS") != "1"
        or not MIGRATION_DATABASE_URL
        or not APP_DATABASE_URL,
        reason="set integration, migration, and restricted RLS database URLs",
    ),
]


@pytest.mark.asyncio
async def test_restricted_role_cannot_read_or_write_another_tenant_with_raw_sql() -> None:
    suffix = uuid4().hex
    tenant_a = f"tenant-rls-a-{suffix}"
    tenant_b = f"tenant-rls-b-{suffix}"
    migrator = DatabaseSessionManager(MIGRATION_DATABASE_URL)
    application = DatabaseSessionManager(APP_DATABASE_URL)
    now = datetime.now(UTC)
    try:
        with tenant_scope(tenant_a):
            async with migrator.session_factory.begin() as session:
                session.add(CustomerModel(tenant_id=tenant_a, customer_id="a", display_name="A"))
        with tenant_scope(tenant_b):
            async with migrator.session_factory.begin() as session:
                session.add(CustomerModel(tenant_id=tenant_b, customer_id="b", display_name="B"))

        with tenant_scope(tenant_a):
            async with application.session_factory() as session:
                rows = list(await session.scalars(select(CustomerModel)))
                raw_rows = (
                    await session.execute(
                        text("SELECT tenant_id, customer_id FROM customers ORDER BY tenant_id")
                    )
                ).all()
            assert [(item.tenant_id, item.customer_id) for item in rows] == [(tenant_a, "a")]
            assert raw_rows == [(tenant_a, "a")]

        async with application.session_factory() as session:
            assert list(await session.scalars(select(CustomerModel))) == []

        with tenant_scope(tenant_a):
            with pytest.raises(DBAPIError):
                async with application.session_factory.begin() as session:
                    session.add(
                        CustomerModel(
                            tenant_id=tenant_b,
                            customer_id="forbidden",
                            display_name="Forbidden",
                            created_at=now,
                        )
                    )
    finally:
        for tenant_id in (tenant_a, tenant_b):
            with tenant_scope(tenant_id):
                async with migrator.session_factory.begin() as session:
                    await session.execute(
                        delete(CustomerModel).where(CustomerModel.tenant_id == tenant_id)
                    )
        await application.dispose()
        await migrator.dispose()


@pytest.mark.asyncio
async def test_platform_site_directory_is_cross_tenant_without_weakening_site_rls() -> None:
    suffix = uuid4().hex
    tenant_a = f"tenant-directory-a-{suffix}"
    tenant_b = f"tenant-directory-b-{suffix}"
    owner_a = f"owner-directory-a-{suffix}"
    owner_b = f"owner-directory-b-{suffix}"
    migrator = DatabaseSessionManager(MIGRATION_DATABASE_URL)
    application = DatabaseSessionManager(APP_DATABASE_URL)
    now = datetime.now(UTC)
    try:
        async with migrator.session_factory.begin() as session:
            session.add_all(
                [
                    TenantModel(
                        tenant_id=tenant_a,
                        name="Directory Workspace A",
                        status="active",
                        created_at=now,
                        updated_at=now,
                    ),
                    TenantModel(
                        tenant_id=tenant_b,
                        name="Directory Workspace B",
                        status="active",
                        created_at=now,
                        updated_at=now,
                    ),
                    IdentityUserModel(
                        user_id=owner_a,
                        display_name="Owner A",
                        status="active",
                        created_at=now,
                        updated_at=now,
                    ),
                    IdentityUserModel(
                        user_id=owner_b,
                        display_name="Owner B",
                        status="active",
                        created_at=now,
                        updated_at=now,
                    ),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    EmailIdentityModel(
                        identity_id=f"email-{owner_a}",
                        user_id=owner_a,
                        normalized_email=f"{owner_a}@example.com",
                        display_email=f"{owner_a}@example.com",
                        status="active",
                        verified_at=now,
                        created_at=now,
                        updated_at=now,
                    ),
                    EmailIdentityModel(
                        identity_id=f"email-{owner_b}",
                        user_id=owner_b,
                        normalized_email=f"{owner_b}@example.com",
                        display_email=f"{owner_b}@example.com",
                        status="active",
                        verified_at=now,
                        created_at=now,
                        updated_at=now,
                    ),
                    TenantMembershipModel(
                        membership_id=f"membership-{owner_a}",
                        tenant_id=tenant_b,
                        user_id=owner_a,
                        roles=["tenant_owner"],
                        scopes=[],
                        status="active",
                        source="admin",
                        approval_status="approved",
                        activated_at=now,
                        deactivated_at=None,
                        created_by="integration-test",
                        created_at=now,
                        updated_at=now,
                    ),
                    TenantMembershipModel(
                        membership_id=f"membership-{owner_b}",
                        tenant_id=tenant_b,
                        user_id=owner_b,
                        roles=["tenant_owner"],
                        scopes=[],
                        status="active",
                        source="admin",
                        approval_status="approved",
                        activated_at=now,
                        deactivated_at=None,
                        created_by="integration-test",
                        created_at=now,
                        updated_at=now,
                    ),
                ]
            )

        for tenant_id, name, status, verification_status in (
            (tenant_a, "Site A", "active", "verified"),
            (tenant_b, "Site B", "disabled", "pending"),
        ):
            with tenant_scope(tenant_id):
                async with migrator.session_factory.begin() as session:
                    subscription = TenantSubscriptionModel(
                        tenant_id=tenant_id,
                        plan_id="trial" if tenant_id == tenant_a else "enterprise",
                        status="trial" if tenant_id == tenant_a else "active",
                        created_at=now,
                        updated_at=now,
                    )
                    quota = TenantQuotaModel(
                        tenant_id=tenant_id,
                        site_limit=2 if tenant_id == tenant_a else 7,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add_all((subscription, quota))
                    site = SupportSiteModel(
                        tenant_id=tenant_id,
                        site_id="shared-site-id",
                        public_widget_id=(
                            f"site_pub_{'a' if tenant_id == tenant_a else 'b'}_{suffix[:20]}"
                        ),
                        name=name,
                        base_url=f"https://{tenant_id}.example.com",
                        allowed_origins=[f"https://{tenant_id}.example.com"],
                        widget_daily_message_limit=500,
                        primary_language="zh-CN",
                        status=status,
                        verification_status=verification_status,
                        verification_expires_at=(
                            now - timedelta(minutes=1) if verification_status == "pending" else None
                        ),
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(site)

        with tenant_scope(tenant_a):
            async with application.session_factory() as session:
                tenant_visible_sites = list(await session.scalars(select(SupportSiteModel)))
                projection_privileges = (
                    await session.execute(
                        text(
                            """
                            SELECT
                                has_table_privilege(
                                    current_user, 'platform_site_directory', 'SELECT'
                                ),
                                has_table_privilege(
                                    current_user, 'platform_site_directory', 'INSERT'
                                ) OR has_table_privilege(
                                    current_user, 'platform_site_directory', 'UPDATE'
                                ) OR has_table_privilege(
                                    current_user, 'platform_site_directory', 'DELETE'
                                ),
                                has_table_privilege(
                                    current_user, 'platform_tenant_entitlements', 'SELECT'
                                ),
                                has_table_privilege(
                                    current_user,
                                    'platform_tenant_entitlements',
                                    'INSERT'
                                ) OR has_table_privilege(
                                    current_user,
                                    'platform_tenant_entitlements',
                                    'UPDATE'
                                ) OR has_table_privilege(
                                    current_user,
                                    'platform_tenant_entitlements',
                                    'DELETE'
                                )
                            """
                        )
                    )
                ).one()
            assert [(item.tenant_id, item.site_id) for item in tenant_visible_sites] == [
                (tenant_a, "shared-site-id")
            ]
            assert tuple(projection_privileges) == (True, False, True, False)

            service = PlatformIdentityService(
                PostgreSQLWorkforceIdentityStore(application.session_factory),
                PostgreSQLWorkforceIdentityStore(application.session_factory),
            )
            result_principal = AuthenticatedPrincipal(
                subject_id="platform-auditor",
                tenant_id=tenant_a,
                roles=frozenset({"tenant_owner"}),
                scopes=frozenset(),
                authentication_method="integration-test",
                authenticated_at=now,
                correlation_id="integration-test",
                platform_roles=frozenset({"platform_auditor"}),
            )
            result = await service.list_site_records(
                ListPlatformSiteRecordsQuery(
                    principal=result_principal,
                    limit=100,
                )
            )
            first_page = await service.list_site_records(
                ListPlatformSiteRecordsQuery(
                    principal=result_principal,
                    limit=1,
                )
            )
            second_page = await service.list_site_records(
                ListPlatformSiteRecordsQuery(
                    principal=result_principal,
                    cursor=first_page.next_cursor,
                    limit=1,
                )
            )
            expired_sites = await service.list_site_records(
                ListPlatformSiteRecordsQuery(
                    principal=result_principal,
                    verification_status="expired",
                    limit=100,
                )
            )
            pending_sites = await service.list_site_records(
                ListPlatformSiteRecordsQuery(
                    principal=result_principal,
                    verification_status="pending",
                    limit=100,
                )
            )
            tenant_b_record = await service.get_tenant_record(
                GetPlatformTenantQuery(
                    principal=result_principal,
                    tenant_id=tenant_b,
                )
            )

        assert {(item.tenant_id, item.site_id) for item in result.items} == {
            (tenant_a, "shared-site-id"),
            (tenant_b, "shared-site-id"),
        }
        assert first_page.total == second_page.total == 2
        assert first_page.next_cursor is not None
        assert {
            (first_page.items[0].tenant_id, first_page.items[0].site_id),
            (second_page.items[0].tenant_id, second_page.items[0].site_id),
        } == {
            (tenant_a, "shared-site-id"),
            (tenant_b, "shared-site-id"),
        }
        tenant_b_site = next(item for item in result.items if item.tenant_id == tenant_b)
        assert tenant_b_site.status == "disabled"
        assert tenant_b_site.verification_status == "expired"
        assert [(item.tenant_id, item.site_id) for item in expired_sites.items] == [
            (tenant_b, "shared-site-id")
        ]
        assert pending_sites.items == ()
        assert tenant_b_site.manager_names == ("Owner A", "Owner B")
        assert len(tenant_b_site.manager_emails) == 2
        assert tenant_b_record.site_count == 0
        assert tenant_b_record.disabled_site_count == 1
        assert tenant_b_record.unverified_site_count == 1
        assert tenant_b_record.site_quota_used == 1
        assert tenant_b_record.site_limit == 7
        assert tenant_b_record.plan_id == "enterprise"
        assert tenant_b_record.subscription_status == "active"
        assert tenant_b_record.owner_names == ("Owner A", "Owner B")
        assert len(tenant_b_record.owner_emails) == 2
        tenant_a_site = next(item for item in result.items if item.tenant_id == tenant_a)
        assert tenant_a_site.manager_names == ()
        assert tenant_a_site.manager_emails == ()

        # Removing either entitlement source must keep the other source visible.
        # This exercises the database trigger path used by old application
        # revisions during rolling deploys.
        with tenant_scope(tenant_a):
            async with application.session_factory.begin() as session:
                await session.execute(
                    delete(TenantQuotaModel).where(TenantQuotaModel.tenant_id == tenant_a)
                )
            tenant_a_without_quota = await service.get_tenant_record(
                GetPlatformTenantQuery(principal=result_principal, tenant_id=tenant_a)
            )
            assert tenant_a_without_quota.site_limit is None
            assert tenant_a_without_quota.plan_id == "trial"

            async with application.session_factory.begin() as session:
                session.add(
                    TenantQuotaModel(
                        tenant_id=tenant_a,
                        site_limit=3,
                        created_at=now,
                        updated_at=now,
                    )
                )
            async with application.session_factory.begin() as session:
                await session.execute(
                    delete(TenantSubscriptionModel).where(
                        TenantSubscriptionModel.tenant_id == tenant_a
                    )
                )
            tenant_a_without_subscription = await service.get_tenant_record(
                GetPlatformTenantQuery(principal=result_principal, tenant_id=tenant_a)
            )
            assert tenant_a_without_subscription.site_limit == 3
            assert tenant_a_without_subscription.plan_id is None
            assert tenant_a_without_subscription.subscription_status is None
    finally:
        async with migrator.session_factory.begin() as session:
            await session.execute(
                delete(TenantModel).where(TenantModel.tenant_id.in_((tenant_a, tenant_b)))
            )
            await session.execute(
                delete(IdentityUserModel).where(IdentityUserModel.user_id.in_((owner_a, owner_b)))
            )
        await application.dispose()
        await migrator.dispose()
