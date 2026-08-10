import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import DBAPIError

from app.application.tenant_context import tenant_scope
from app.domain.models.web_sync_worker import WebSyncWorkerRegistration
from app.integrations.postgres.models.identity import TenantModel
from app.integrations.postgres.models.web_sync_workers import WebSyncWorkerRegistrationModel
from app.integrations.postgres.session import DatabaseSessionManager
from app.integrations.postgres.web_sync_workers import (
    PostgreSQLActiveTenantCatalog,
    PostgreSQLWebSyncWorkerRegistry,
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
MIGRATION_DATABASE_URL = os.getenv("MIGRATION_DATABASE_URL", "")
RLS_DATABASE_URL = os.getenv("RLS_DATABASE_URL", "")


async def test_catalog_and_registry_are_dynamic_and_monotonic() -> None:
    suffix = uuid4().hex
    tenant_a = f"worker-a-{suffix}"
    tenant_b = f"worker-b-{suffix}"
    tenant_disabled = f"worker-disabled-{suffix}"
    now = datetime.now(UTC).replace(microsecond=0)
    manager = DatabaseSessionManager(DATABASE_URL)
    catalog = PostgreSQLActiveTenantCatalog(manager.session_factory)
    registry = PostgreSQLWebSyncWorkerRegistry(manager.session_factory)
    try:
        async with manager.session_factory.begin() as session:
            session.add_all(
                TenantModel(
                    tenant_id=tenant_id,
                    name=tenant_id,
                    status="disabled" if tenant_id == tenant_disabled else "active",
                    created_at=now,
                    updated_at=now,
                )
                for tenant_id in (tenant_b, tenant_disabled, tenant_a)
            )

        active_tenant_ids = await catalog.list_active_tenant_ids()
        relevant_tenant_ids = tuple(
            tenant_id
            for tenant_id in active_tenant_ids
            if tenant_id in (tenant_a, tenant_b, tenant_disabled)
        )
        assert relevant_tenant_ids == tuple(sorted((tenant_a, tenant_b)))

        first = WebSyncWorkerRegistration(
            tenant_id=tenant_a,
            worker_instance_id="worker-instance-1",
            started_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(seconds=30),
            capacity_total=4,
            capacity_in_use=1,
        )
        with tenant_scope(tenant_a):
            assert await registry.register_or_heartbeat(first) == first
            advanced = replace(
                first,
                last_seen_at=now + timedelta(seconds=10),
                expires_at=now + timedelta(seconds=40),
                capacity_in_use=3,
            )
            assert await registry.register_or_heartbeat(advanced) == advanced

            stale = replace(
                first,
                health_state="unavailable",
                last_seen_at=now + timedelta(seconds=5),
                expires_at=now + timedelta(seconds=35),
                capacity_in_use=4,
            )
            persisted = await registry.register_or_heartbeat(stale)
            assert persisted == advanced
            assert await registry.list_current(
                tenant_id=tenant_a,
                observed_at=now + timedelta(seconds=20),
            ) == (advanced,)
            assert (
                await registry.list_current(
                    tenant_id=tenant_a,
                    observed_at=now + timedelta(seconds=41),
                )
                == ()
            )
            assert (
                await registry.remove_expired(
                    tenant_id=tenant_a,
                    observed_at=now + timedelta(seconds=41),
                )
                == 1
            )
            assert (
                await registry.unregister(
                    tenant_id=tenant_a,
                    worker_instance_id=first.worker_instance_id,
                )
                is False
            )

        tenant_b_registration = replace(first, tenant_id=tenant_b)
        with tenant_scope(tenant_b):
            assert (
                await registry.register_or_heartbeat(tenant_b_registration) == tenant_b_registration
            )
            assert (
                await registry.unregister(
                    tenant_id=tenant_b,
                    worker_instance_id=first.worker_instance_id,
                )
                is True
            )
    finally:
        for tenant_id in (tenant_a, tenant_b, tenant_disabled):
            with tenant_scope(tenant_id):
                async with manager.session_factory.begin() as session:
                    await session.execute(
                        delete(WebSyncWorkerRegistrationModel).where(
                            WebSyncWorkerRegistrationModel.tenant_id == tenant_id
                        )
                    )
        async with manager.session_factory.begin() as session:
            await session.execute(
                delete(TenantModel).where(
                    TenantModel.tenant_id.in_((tenant_a, tenant_b, tenant_disabled))
                )
            )
        await manager.dispose()


@pytest.mark.skipif(
    not MIGRATION_DATABASE_URL or not RLS_DATABASE_URL,
    reason="set migration and restricted RLS database URLs",
)
async def test_worker_registry_is_isolated_by_forced_rls() -> None:
    suffix = uuid4().hex
    tenant_a = f"worker-rls-a-{suffix}"
    tenant_b = f"worker-rls-b-{suffix}"
    now = datetime.now(UTC).replace(microsecond=0)
    migrator = DatabaseSessionManager(MIGRATION_DATABASE_URL)
    application = DatabaseSessionManager(RLS_DATABASE_URL)
    registry = PostgreSQLWebSyncWorkerRegistry(application.session_factory)
    registration_a = WebSyncWorkerRegistration(
        tenant_id=tenant_a,
        worker_instance_id="worker-a",
        started_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(seconds=30),
    )
    registration_b = replace(
        registration_a,
        tenant_id=tenant_b,
        worker_instance_id="worker-b",
    )
    try:
        async with migrator.session_factory.begin() as session:
            session.add_all(
                TenantModel(
                    tenant_id=tenant_id,
                    name=tenant_id,
                    status="active",
                    created_at=now,
                    updated_at=now,
                )
                for tenant_id in (tenant_a, tenant_b)
            )

        with tenant_scope(tenant_a):
            await registry.register_or_heartbeat(registration_a)
        with tenant_scope(tenant_b):
            await registry.register_or_heartbeat(registration_b)

        with tenant_scope(tenant_a):
            async with application.session_factory() as session:
                visible = tuple(await session.scalars(select(WebSyncWorkerRegistrationModel)))
            assert [(item.tenant_id, item.worker_instance_id) for item in visible] == [
                (tenant_a, "worker-a")
            ]
            assert await registry.list_current(tenant_id=tenant_b, observed_at=now) == ()
            with pytest.raises(DBAPIError):
                await registry.register_or_heartbeat(registration_b)

        async with application.session_factory() as session:
            assert tuple(await session.scalars(select(WebSyncWorkerRegistrationModel))) == ()
    finally:
        for tenant_id in (tenant_a, tenant_b):
            with tenant_scope(tenant_id):
                async with migrator.session_factory.begin() as session:
                    await session.execute(
                        delete(WebSyncWorkerRegistrationModel).where(
                            WebSyncWorkerRegistrationModel.tenant_id == tenant_id
                        )
                    )
        async with migrator.session_factory.begin() as session:
            await session.execute(
                delete(TenantModel).where(TenantModel.tenant_id.in_((tenant_a, tenant_b)))
            )
        await application.dispose()
        await migrator.dispose()
