import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, update

from app.application.dto import (
    AuthenticateAdminCommand,
    BootstrapAdminCommand,
    ListAdminSessionsQuery,
    ListAuditEventsQuery,
    LoginAdminCommand,
    RevokeAdminSessionCommand,
)
from app.application.services import AdminSessionService, AuditLogService
from app.integrations.auth import ScryptPasswordHasher
from app.integrations.postgres import PostgreSQLAdminIdentityStore, PostgreSQLAuditLogAdapter
from app.integrations.postgres.models import AdminSessionModel, AdminUserModel, AuditEventModel
from app.integrations.postgres.session import DatabaseSessionManager

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
async def test_session_management_and_audit_query_are_persisted() -> None:
    suffix = uuid4().hex
    tenant_id = f"tenant-governance-{suffix}"
    manager = DatabaseSessionManager(DATABASE_URL)
    identity_store = PostgreSQLAdminIdentityStore(manager.session_factory)
    sessions = AdminSessionService(
        identity_store=identity_store,
        password_hasher=ScryptPasswordHasher(),
        session_ttl_seconds=3600,
    )
    audit = AuditLogService(PostgreSQLAuditLogAdapter(manager.session_factory))

    try:
        user = await sessions.bootstrap_admin(
            BootstrapAdminCommand(
                tenant_id=tenant_id,
                username=f"owner-{suffix}",
                password="integration-admin-password",
                display_name="Owner",
            )
        )
        first = await sessions.login(
            LoginAdminCommand(
                tenant_id,
                user.username,
                "integration-admin-password",
                "login-first",
                "fingerprint-first",
            )
        )
        second = await sessions.login(
            LoginAdminCommand(
                tenant_id,
                user.username,
                "integration-admin-password",
                "login-second",
                "fingerprint-second",
            )
        )
        stale_activity = datetime.now(UTC) - timedelta(minutes=5)
        async with manager.session_factory.begin() as database_session:
            await database_session.execute(
                update(AdminSessionModel)
                .where(AdminSessionModel.tenant_id == tenant_id)
                .values(last_seen_at=stale_activity)
            )
        principal = (
            await sessions.authenticate(
                AuthenticateAdminCommand(first.session.token, "authenticate-first")
            )
        ).principal
        listed = await sessions.list_sessions(
            ListAdminSessionsQuery(principal, first.session.token)
        )
        second_session = next(item for item in listed.items if not item.is_current)

        revoked = await sessions.revoke_managed_session(
            RevokeAdminSessionCommand(
                principal=principal,
                current_session_token=first.session.token,
                session_id=second_session.session_id,
                correlation_id="revoke-second",
            )
        )
        repeated = await sessions.revoke_managed_session(
            RevokeAdminSessionCommand(
                principal=principal,
                current_session_token=first.session.token,
                session_id=second_session.session_id,
                correlation_id="revoke-second-retry",
            )
        )
        async with manager.session_factory.begin() as database_session:
            database_session.add(
                AuditEventModel(
                    tenant_id=tenant_id,
                    event_id=f"sensitive-{suffix}",
                    event_type="test.sensitive",
                    actor_subject_id=user.user_id,
                    correlation_id="sensitive-correlation",
                    resource_type="test",
                    resource_id="test-1",
                    details={"password": "never-return", "safe": "visible"},
                    created_at=datetime.now(UTC),
                )
            )
        events = await audit.list_events(ListAuditEventsQuery(principal=principal, limit=100))

        assert len(listed.items) == 2
        assert next(item for item in listed.items if item.is_current).last_seen_at > stale_activity
        assert revoked.changed is True
        assert repeated.changed is False
        sensitive = next(item for item in events.items if item.event_type == "test.sensitive")
        assert sensitive.details == {"password": "[REDACTED]", "safe": "visible"}
        assert any(item.event_type == "admin_session.revoked" for item in events.items)
        with pytest.raises(PermissionError, match="invalid or expired"):
            await sessions.authenticate(
                AuthenticateAdminCommand(second.session.token, "authenticate-revoked")
            )
    finally:
        async with manager.session_factory.begin() as session:
            await session.execute(
                delete(AuditEventModel).where(AuditEventModel.tenant_id == tenant_id)
            )
            await session.execute(
                delete(AdminSessionModel).where(AdminSessionModel.tenant_id == tenant_id)
            )
            await session.execute(
                delete(AdminUserModel).where(AdminUserModel.tenant_id == tenant_id)
            )
        await manager.dispose()
