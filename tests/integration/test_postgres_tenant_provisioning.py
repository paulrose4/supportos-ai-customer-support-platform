import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from app.application.tenant_context import tenant_scope
from app.domain.models import Tenant
from app.integrations.postgres.models import (
    PlatformAuditEventModel,
    SupportQueueModel,
    TenantModel,
    TenantSettingsModel,
)
from app.integrations.postgres.session import DatabaseSessionManager
from app.integrations.postgres.workforce_identity import PostgreSQLWorkforceIdentityStore

APP_DATABASE_URL = os.getenv("RLS_DATABASE_URL", "")
MIGRATION_DATABASE_URL = os.getenv("MIGRATION_DATABASE_URL", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION_TESTS") != "1"
        or not APP_DATABASE_URL
        or not MIGRATION_DATABASE_URL,
        reason="set integration, migration, and restricted RLS database URLs",
    ),
]


@pytest.mark.asyncio
async def test_tenant_provisioning_is_idempotent_and_creates_human_order_queue() -> None:
    tenant_id = f"tenant-provision-{uuid4().hex}"
    now = datetime.now(UTC)
    tenant = Tenant(tenant_id, "Provisioned Tenant", "active", now, now)
    application = DatabaseSessionManager(APP_DATABASE_URL)
    migrator = DatabaseSessionManager(MIGRATION_DATABASE_URL)
    store = PostgreSQLWorkforceIdentityStore(application.session_factory)
    try:
        first = await store.provision_tenant(tenant, actor_subject_id="platform-owner")
        second = await store.provision_tenant(tenant, actor_subject_id="platform-owner")
        assert first == second

        with tenant_scope(tenant_id):
            async with application.session_factory() as session:
                queues = list(
                    await session.scalars(
                        select(SupportQueueModel).order_by(SupportQueueModel.queue_id)
                    )
                )
                settings = await session.scalar(
                    select(TenantSettingsModel).where(TenantSettingsModel.tenant_id == tenant_id)
                )
                audit_count = await session.scalar(
                    select(func.count())
                    .select_from(PlatformAuditEventModel)
                    .where(
                        PlatformAuditEventModel.event_type == "tenant.provisioned",
                        PlatformAuditEventModel.resource_id == tenant_id,
                    )
                )
        assert [(item.queue_id, item.is_default) for item in queues] == [
            ("general", True),
            ("orders", False),
        ]
        assert settings is not None
        assert settings.timezone == "Asia/Shanghai"
        assert audit_count == 1
    finally:
        with tenant_scope(tenant_id):
            async with migrator.session_factory.begin() as session:
                await session.execute(
                    delete(SupportQueueModel).where(SupportQueueModel.tenant_id == tenant_id)
                )
                await session.execute(
                    delete(PlatformAuditEventModel).where(
                        PlatformAuditEventModel.resource_id == tenant_id
                    )
                )
                await session.execute(delete(TenantModel).where(TenantModel.tenant_id == tenant_id))
        await application.dispose()
        await migrator.dispose()
