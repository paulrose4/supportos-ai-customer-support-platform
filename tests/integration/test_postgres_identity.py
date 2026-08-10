import os
from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from app.application.dto import (
    AuthenticateAdminCommand,
    BootstrapAdminCommand,
    ChangeAdminPasswordCommand,
    CreateAdminUserCommand,
    ListAdminUsersQuery,
    LoginAdminCommand,
    LogoutAdminCommand,
    ResetAdminUserPasswordCommand,
    UpdateAdminUserCommand,
)
from app.application.services import (
    AdminLoginThrottledError,
    AdminSessionService,
    AdminUserManagementService,
)
from app.integrations.auth import ScryptPasswordHasher
from app.integrations.postgres.identity import PostgreSQLAdminIdentityStore
from app.integrations.postgres.models import (
    AdminLoginThrottleModel,
    AdminSessionModel,
    AdminUserModel,
    AuditEventModel,
)
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
async def test_admin_identity_session_lifecycle_is_persisted_and_audited() -> None:
    suffix = uuid4().hex
    tenant_id = f"tenant-identity-{suffix}"
    username = f"owner-{suffix}"
    source_fingerprint = f"source-{suffix}"
    password = "integration-admin-password"
    manager = DatabaseSessionManager(DATABASE_URL)
    store = PostgreSQLAdminIdentityStore(manager.session_factory)
    service = AdminSessionService(
        identity_store=store,
        password_hasher=ScryptPasswordHasher(),
        session_ttl_seconds=3600,
    )

    try:
        bootstrapped = await service.bootstrap_admin(
            BootstrapAdminCommand(
                tenant_id=tenant_id,
                username=username,
                password=password,
                display_name="Integration Tenant Owner",
            )
        )
        second_bootstrap = await service.bootstrap_admin(
            BootstrapAdminCommand(
                tenant_id=tenant_id,
                username=username,
                password="different-password",
                display_name="Ignored Duplicate",
            )
        )
        login = await service.login(
            LoginAdminCommand(
                tenant_id,
                username,
                password,
                "correlation-login",
                source_fingerprint,
            )
        )
        authenticated = await service.authenticate(
            AuthenticateAdminCommand(login.session.token, "correlation-authenticate")
        )

        async with manager.session_factory() as session:
            user_count = await session.scalar(
                select(func.count())
                .select_from(AdminUserModel)
                .where(AdminUserModel.tenant_id == tenant_id)
            )
            session_model = await session.scalar(
                select(AdminSessionModel).where(AdminSessionModel.tenant_id == tenant_id)
            )
            audit_types = list(
                await session.scalars(
                    select(AuditEventModel.event_type)
                    .where(AuditEventModel.tenant_id == tenant_id)
                    .order_by(AuditEventModel.created_at)
                )
            )

        assert bootstrapped.user_id == second_bootstrap.user_id
        assert user_count == 1
        assert authenticated.user.display_name == "Integration Tenant Owner"
        assert authenticated.principal.tenant_id == tenant_id
        assert "support:inbox:write" in authenticated.principal.scopes
        assert session_model is not None
        assert session_model.token_hash == sha256(login.session.token.encode("utf-8")).hexdigest()
        assert session_model.token_hash != login.session.token
        assert audit_types == ["admin_user.bootstrapped", "admin_session.created"]

        assert await service.logout(LogoutAdminCommand(login.session.token, "correlation-logout"))
        assert await service.logout(
            LogoutAdminCommand(login.session.token, "correlation-logout-retry")
        )
        with pytest.raises(PermissionError):
            await service.authenticate(
                AuthenticateAdminCommand(login.session.token, "correlation-after-logout")
            )

        async with manager.session_factory() as session:
            revoked_at = await session.scalar(
                select(AdminSessionModel.revoked_at).where(AdminSessionModel.tenant_id == tenant_id)
            )
            revoked_audit_count = await session.scalar(
                select(func.count())
                .select_from(AuditEventModel)
                .where(
                    AuditEventModel.tenant_id == tenant_id,
                    AuditEventModel.event_type == "admin_session.revoked",
                )
            )

        assert revoked_at is not None
        assert revoked_audit_count == 1
    finally:
        await _cleanup(manager, tenant_id, source_fingerprint)


@pytest.mark.asyncio
async def test_login_throttle_and_password_change_are_persisted_and_audited() -> None:
    suffix = uuid4().hex
    tenant_id = f"tenant-security-{suffix}"
    username = f"owner-{suffix}"
    blocked_source = f"blocked-{suffix}"
    trusted_source = f"trusted-{suffix}"
    manager = DatabaseSessionManager(DATABASE_URL)
    service = AdminSessionService(
        identity_store=PostgreSQLAdminIdentityStore(manager.session_factory),
        password_hasher=ScryptPasswordHasher(),
        session_ttl_seconds=3600,
        login_max_attempts=2,
        login_window_seconds=300,
        login_lockout_seconds=300,
    )
    try:
        await service.bootstrap_admin(
            BootstrapAdminCommand(
                tenant_id,
                username,
                "integration-admin-password",
                "Security Owner",
            )
        )
        wrong = LoginAdminCommand(
            tenant_id,
            username,
            "wrong-password",
            "correlation-failure",
            blocked_source,
        )
        with pytest.raises(PermissionError, match="invalid username or password"):
            await service.login(wrong)
        with pytest.raises(AdminLoginThrottledError):
            await service.login(wrong)

        login = await service.login(
            LoginAdminCommand(
                tenant_id,
                username,
                "integration-admin-password",
                "correlation-login",
                trusted_source,
            )
        )
        await service.change_password(
            ChangeAdminPasswordCommand(
                login.session.token,
                "integration-admin-password",
                "integration-new-password",
                "correlation-password-change",
            )
        )

        with pytest.raises(PermissionError):
            await service.authenticate(
                AuthenticateAdminCommand(login.session.token, "correlation-old-session")
            )
        new_login = await service.login(
            LoginAdminCommand(
                tenant_id,
                username,
                "integration-new-password",
                "correlation-new-login",
                trusted_source,
            )
        )
        assert new_login.session.user.username == username

        async with manager.session_factory() as session:
            throttle = await session.scalar(
                select(AdminLoginThrottleModel).where(
                    AdminLoginThrottleModel.source_fingerprint == blocked_source
                )
            )
            audit_types = list(
                await session.scalars(
                    select(AuditEventModel.event_type).where(AuditEventModel.tenant_id == tenant_id)
                )
            )
        assert throttle is not None
        assert throttle.failure_count == 2
        assert throttle.locked_until is not None
        assert audit_types.count("admin_login.failed") == 2
        assert "admin_user.password_changed" in audit_types
    finally:
        await _cleanup(manager, tenant_id, blocked_source, trusted_source)


async def _cleanup(
    manager: DatabaseSessionManager,
    tenant_id: str,
    *source_fingerprints: str,
) -> None:
    async with manager.session_factory.begin() as session:
        await session.execute(
            delete(AdminSessionModel).where(AdminSessionModel.tenant_id == tenant_id)
        )
        await session.execute(
            delete(AdminLoginThrottleModel).where(
                AdminLoginThrottleModel.source_fingerprint.in_(source_fingerprints)
            )
        )
        await session.execute(delete(AdminUserModel).where(AdminUserModel.tenant_id == tenant_id))
        await session.execute(delete(AuditEventModel).where(AuditEventModel.tenant_id == tenant_id))
    await manager.dispose()


@pytest.mark.asyncio
async def test_admin_user_management_is_transactional_and_audited() -> None:
    suffix = uuid4().hex
    tenant_id = f"tenant-team-{suffix}"
    manager = DatabaseSessionManager(DATABASE_URL)
    store = PostgreSQLAdminIdentityStore(manager.session_factory)
    hasher = ScryptPasswordHasher()
    sessions = AdminSessionService(
        identity_store=store,
        password_hasher=hasher,
        session_ttl_seconds=3600,
    )
    users = AdminUserManagementService(identity_store=store, password_hasher=hasher)

    try:
        owner = await sessions.bootstrap_admin(
            BootstrapAdminCommand(tenant_id, f"owner-{suffix}", "owner-test-password", "Owner")
        )
        owner_login = await sessions.login(
            LoginAdminCommand(tenant_id, owner.username, "owner-test-password", "owner-login")
        )
        owner_principal = (
            await sessions.authenticate(
                AuthenticateAdminCommand(owner_login.session.token, "owner-auth")
            )
        ).principal
        create = CreateAdminUserCommand(
            principal=owner_principal,
            username=f"agent-{suffix}",
            display_name="Support Agent",
            password="agent-test-password",
            roles=frozenset({"support_agent"}),
            correlation_id="agent-create",
        )
        agent = await users.create_user(create)
        repeated = await users.create_user(create)
        agent_login = await sessions.login(
            LoginAdminCommand(tenant_id, agent.username, "agent-test-password", "agent-login")
        )

        updated = await users.update_user(
            UpdateAdminUserCommand(
                principal=owner_principal,
                user_id=agent.user_id,
                display_name="Support Manager",
                roles=frozenset({"support_manager"}),
                status="active",
                correlation_id="agent-update",
            )
        )
        await users.reset_password(
            ResetAdminUserPasswordCommand(
                principal=owner_principal,
                user_id=agent.user_id,
                new_password="agent-replacement-password",
                correlation_id="agent-reset",
            )
        )
        listed = await users.list_users(ListAdminUsersQuery(owner_principal))

        assert repeated.user_id == agent.user_id
        assert updated.roles == frozenset({"support_manager"})
        assert {item.user_id for item in listed.items} == {owner.user_id, agent.user_id}
        with pytest.raises(PermissionError, match="invalid or expired"):
            await sessions.authenticate(
                AuthenticateAdminCommand(agent_login.session.token, "agent-after-update")
            )

        async with manager.session_factory() as session:
            audit_types = list(
                await session.scalars(
                    select(AuditEventModel.event_type)
                    .where(AuditEventModel.tenant_id == tenant_id)
                    .order_by(AuditEventModel.created_at)
                )
            )
        assert audit_types.count("admin_user.created") == 1
        assert audit_types.count("admin_user.updated") == 1
        assert audit_types.count("admin_user.password_reset") == 1
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
