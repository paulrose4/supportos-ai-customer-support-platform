import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from app.application.dto import (
    ExecuteRetentionCommand,
    PreviewRetentionCommand,
    RetentionPolicy,
)
from app.application.services import RetentionService
from app.integrations.postgres import DatabaseSessionManager, PostgreSQLRetentionAdapter
from app.integrations.postgres.models import (
    AdminSessionModel,
    AdminUserModel,
    AuditEventModel,
    CustomerMemoryItemModel,
    CustomerModel,
    IdentityUserModel,
    SupportOperationRequestModel,
    TenantMembershipModel,
    TenantModel,
)

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


@pytest.mark.asyncio
async def test_retention_is_tenant_scoped_transactional_audited_and_idempotent() -> None:
    suffix = uuid4().hex
    tenant_id = f"tenant-retention-{suffix}"
    other_tenant = f"tenant-retention-other-{suffix}"
    now = datetime.now(UTC)
    old = now - timedelta(days=500)
    global_user_id = f"retention-owner-{suffix}"
    manager = DatabaseSessionManager(DATABASE_URL)
    service = RetentionService(
        PostgreSQLRetentionAdapter(manager.session_factory), execution_enabled=True
    )
    policy = RetentionPolicy("privacy-v1", 30, 7, 90, 365)

    try:
        async with manager.session_factory.begin() as session:
            session.add_all(
                [
                    TenantModel(
                        tenant_id=tenant_id,
                        name="Retention Test Tenant",
                        status="active",
                        created_at=old,
                        updated_at=now,
                    ),
                    IdentityUserModel(
                        user_id=global_user_id,
                        display_name="Owner",
                        status="active",
                        created_at=old,
                        updated_at=now,
                    ),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    TenantMembershipModel(
                        membership_id=f"membership-{suffix}",
                        tenant_id=tenant_id,
                        user_id=global_user_id,
                        roles=["tenant_owner"],
                        scopes=[],
                        status="active",
                        created_at=old,
                        updated_at=now,
                    ),
                    AdminUserModel(
                        tenant_id=tenant_id,
                        user_id="owner-1",
                        global_user_id=global_user_id,
                        username=f"owner-{suffix}",
                        display_name="Owner",
                        password_hash="not-a-real-password-hash",
                        roles=["tenant_owner"],
                        scopes=[],
                        status="active",
                        created_at=old,
                        updated_at=now,
                    ),
                ]
            )
            session.add(
                CustomerModel(
                    tenant_id=tenant_id,
                    customer_id="customer-1",
                    display_name="Synthetic Customer",
                    created_at=old,
                )
            )
            await session.flush()
            session.add_all(
                [
                    AdminSessionModel(
                        tenant_id=tenant_id,
                        session_id="old-session",
                        user_id=global_user_id,
                        token_hash=uuid4().hex + uuid4().hex,
                        expires_at=old,
                        created_at=old,
                        last_seen_at=old,
                        source_fingerprint="old-source",
                        revoked_at=old,
                    ),
                    AdminSessionModel(
                        tenant_id=tenant_id,
                        session_id="current-session",
                        user_id=global_user_id,
                        token_hash=uuid4().hex + uuid4().hex,
                        expires_at=now + timedelta(days=1),
                        created_at=now,
                        last_seen_at=now,
                        source_fingerprint="current-source",
                        revoked_at=None,
                    ),
                    CustomerMemoryItemModel(
                        tenant_id=tenant_id,
                        memory_id="expired-memory",
                        customer_id="customer-1",
                        kind="preference",
                        content="synthetic expired memory",
                        source_type="test",
                        source_id="test-1",
                        confidence=1.0,
                        consent_status="granted",
                        expires_at=old,
                        created_at=old,
                        updated_at=old,
                    ),
                    CustomerMemoryItemModel(
                        tenant_id=tenant_id,
                        memory_id="current-memory",
                        customer_id="customer-1",
                        kind="preference",
                        content="synthetic current memory",
                        source_type="test",
                        source_id="test-2",
                        confidence=1.0,
                        consent_status="granted",
                        expires_at=None,
                        created_at=now,
                        updated_at=now,
                    ),
                    SupportOperationRequestModel(
                        tenant_id=tenant_id,
                        idempotency_key="old-operation",
                        operation="test",
                        resource_type="test",
                        resource_id="old",
                        response_snapshot={},
                        created_at=old,
                    ),
                    SupportOperationRequestModel(
                        tenant_id=tenant_id,
                        idempotency_key="current-operation",
                        operation="test",
                        resource_type="test",
                        resource_id="current",
                        response_snapshot={},
                        created_at=now,
                    ),
                    AuditEventModel(
                        tenant_id=tenant_id,
                        event_id="old-audit",
                        event_type="test.old",
                        resource_type="test",
                        resource_id="old",
                        details={},
                        created_at=old,
                    ),
                    AuditEventModel(
                        tenant_id=tenant_id,
                        event_id="preserved-retention-audit",
                        event_type="retention.executed",
                        resource_type="tenant",
                        resource_id=tenant_id,
                        details={"counts": {}},
                        created_at=old,
                    ),
                    AuditEventModel(
                        tenant_id=other_tenant,
                        event_id="other-old-audit",
                        event_type="test.old",
                        resource_type="test",
                        resource_id="other",
                        details={},
                        created_at=old,
                    ),
                ]
            )

        preview = await service.preview(PreviewRetentionCommand(tenant_id, policy, now))
        command = ExecuteRetentionCommand(
            tenant_id=tenant_id,
            policy=policy,
            evaluated_at=now,
            run_key="retention-run-1",
            approval_reference="PRIVACY-42",
            actor_subject_id="privacy-owner",
            correlation_id="retention-correlation",
        )
        executed = await service.execute(command)
        repeated = await service.execute(command)

        assert preview.counts.admin_sessions == 1
        assert preview.counts.expired_customer_memory == 1
        assert preview.counts.support_operation_requests == 1
        assert preview.counts.audit_events == 1
        assert executed.counts == preview.counts
        assert repeated.counts == executed.counts

        async with manager.session_factory() as session:
            old_session_count = await session.scalar(
                select(func.count())
                .select_from(AdminSessionModel)
                .where(
                    AdminSessionModel.tenant_id == tenant_id,
                    AdminSessionModel.session_id == "old-session",
                )
            )
            current_session_count = await session.scalar(
                select(func.count())
                .select_from(AdminSessionModel)
                .where(
                    AdminSessionModel.tenant_id == tenant_id,
                    AdminSessionModel.session_id == "current-session",
                )
            )
            other_audit_count = await session.scalar(
                select(func.count())
                .select_from(AuditEventModel)
                .where(AuditEventModel.tenant_id == other_tenant)
            )
            retention_audits = await session.scalar(
                select(func.count())
                .select_from(AuditEventModel)
                .where(
                    AuditEventModel.tenant_id == tenant_id,
                    AuditEventModel.event_type == "retention.executed",
                )
            )
        assert old_session_count == 0
        assert current_session_count == 1
        assert other_audit_count == 1
        assert retention_audits == 2
    finally:
        async with manager.session_factory.begin() as session:
            await session.execute(
                delete(CustomerMemoryItemModel).where(
                    CustomerMemoryItemModel.tenant_id == tenant_id
                )
            )
            await session.execute(delete(CustomerModel).where(CustomerModel.tenant_id == tenant_id))
            await session.execute(
                delete(AdminSessionModel).where(AdminSessionModel.tenant_id == tenant_id)
            )
            await session.execute(
                delete(AdminUserModel).where(AdminUserModel.tenant_id == tenant_id)
            )
            await session.execute(
                delete(TenantMembershipModel).where(TenantMembershipModel.tenant_id == tenant_id)
            )
            await session.execute(
                delete(IdentityUserModel).where(IdentityUserModel.user_id == global_user_id)
            )
            await session.execute(delete(TenantModel).where(TenantModel.tenant_id == tenant_id))
            await session.execute(
                delete(SupportOperationRequestModel).where(
                    SupportOperationRequestModel.tenant_id == tenant_id
                )
            )
            await session.execute(
                delete(AuditEventModel).where(
                    AuditEventModel.tenant_id.in_([tenant_id, other_tenant])
                )
            )
        await manager.dispose()
