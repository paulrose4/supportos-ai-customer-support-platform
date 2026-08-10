from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.application.dto import (
    AuthenticateAdminCommand,
    BootstrapAdminCommand,
    ChangeAdminPasswordCommand,
    ListAdminSessionsQuery,
    LoginAdminCommand,
    LogoutAdminCommand,
    RevokeAdminSessionCommand,
)
from app.application.services import AdminLoginThrottledError, AdminSessionService
from app.domain.models import AdminSession, AdminUser
from app.integrations.auth import ScryptPasswordHasher


class InMemoryIdentityStore:
    def __init__(self) -> None:
        self.users: dict[tuple[str, str], AdminUser] = {}
        self.sessions: dict[str, AdminSession] = {}
        self.throttles: dict[str, tuple[int, datetime, datetime | None]] = {}

    async def get_user_by_username(self, *, tenant_id: str, username: str):  # type: ignore[no-untyped-def]
        return self.users.get((tenant_id, username))

    async def get_user_for_session(self, *, token_hash: str):  # type: ignore[no-untyped-def]
        session = self.sessions.get(token_hash)
        if (
            session is None
            or session.revoked_at is not None
            or session.expires_at <= datetime.now(UTC)
        ):
            return None
        return next(
            (
                user
                for user in self.users.values()
                if user.tenant_id == session.tenant_id and user.user_id == session.user_id
            ),
            None,
        )

    async def get_login_lock(
        self, *, source_fingerprint: str, checked_at: datetime
    ) -> datetime | None:
        throttle = self.throttles.get(source_fingerprint)
        if throttle is None or throttle[2] is None or throttle[2] <= checked_at:
            return None
        return throttle[2]

    async def record_login_failure(
        self,
        *,
        source_fingerprint: str,
        tenant_id: str,
        username: str,
        correlation_id: str,
        occurred_at: datetime,
        max_attempts: int,
        window_seconds: int,
        lockout_seconds: int,
    ) -> datetime | None:
        del tenant_id, username, correlation_id
        count, window_started, locked_until = self.throttles.get(
            source_fingerprint, (0, occurred_at, None)
        )
        if window_started + timedelta(seconds=window_seconds) <= occurred_at:
            count, window_started, locked_until = 0, occurred_at, None
        count += 1
        if count >= max_attempts:
            locked_until = occurred_at + timedelta(seconds=lockout_seconds)
        self.throttles[source_fingerprint] = (count, window_started, locked_until)
        return locked_until

    async def clear_login_failures(
        self,
        *,
        source_fingerprint: str,
        tenant_id: str,
        user_id: str,
        correlation_id: str,
        cleared_at: datetime,
    ) -> None:
        del tenant_id, user_id, correlation_id, cleared_at
        self.throttles.pop(source_fingerprint, None)

    async def create_user_if_absent(self, user: AdminUser) -> AdminUser:
        return self.users.setdefault((user.tenant_id, user.username), user)

    async def create_session(self, session: AdminSession) -> None:
        self.sessions[session.token_hash] = session

    async def revoke_session(self, *, token_hash: str, revoked_at: datetime) -> bool:
        session = self.sessions.get(token_hash)
        if session is not None:
            self.sessions[token_hash] = replace(session, revoked_at=revoked_at)
        return True

    async def list_user_sessions(
        self, *, tenant_id: str, user_id: str, limit: int = 50
    ) -> list[AdminSession]:
        sessions = [
            session
            for session in self.sessions.values()
            if session.tenant_id == tenant_id and session.user_id == user_id
        ]
        return sorted(sessions, key=lambda item: item.created_at, reverse=True)[:limit]

    async def revoke_user_session(
        self,
        *,
        tenant_id: str,
        user_id: str,
        session_id: str,
        actor_subject_id: str,
        correlation_id: str,
        revoked_at: datetime,
    ) -> bool | None:
        del actor_subject_id, correlation_id
        for token_hash, session in self.sessions.items():
            if (
                session.tenant_id == tenant_id
                and session.user_id == user_id
                and session.session_id == session_id
            ):
                if session.revoked_at is not None:
                    return False
                self.sessions[token_hash] = replace(session, revoked_at=revoked_at)
                return True
        return None

    async def change_password_and_revoke_sessions(
        self,
        *,
        tenant_id: str,
        user_id: str,
        expected_password_hash: str,
        new_password_hash: str,
        correlation_id: str,
        changed_at: datetime,
    ) -> bool:
        del correlation_id
        user = next(
            (
                item
                for item in self.users.values()
                if item.tenant_id == tenant_id and item.user_id == user_id
            ),
            None,
        )
        if user is None or user.password_hash != expected_password_hash:
            return False
        self.users[(tenant_id, user.username)] = replace(
            user,
            password_hash=new_password_hash,
            updated_at=changed_at,
        )
        for token_hash, session in tuple(self.sessions.items()):
            if session.tenant_id == tenant_id and session.user_id == user_id:
                self.sessions[token_hash] = replace(session, revoked_at=changed_at)
        return True


@pytest.mark.asyncio
async def test_admin_session_login_authenticate_and_logout() -> None:
    store = InMemoryIdentityStore()
    service = AdminSessionService(
        identity_store=store,
        password_hasher=ScryptPasswordHasher(),
        session_ttl_seconds=3600,
    )
    user = await service.bootstrap_admin(
        BootstrapAdminCommand(
            tenant_id="tenant-a",
            username="Owner",
            password="a-secure-password",
            display_name="Tenant Owner",
        )
    )

    login = await service.login(
        LoginAdminCommand("tenant-a", "owner", "a-secure-password", "correlation-1")
    )
    authenticated = await service.authenticate(
        AuthenticateAdminCommand(login.session.token, "correlation-2")
    )
    await service.logout(LogoutAdminCommand(login.session.token, "correlation-3"))

    assert user.roles == frozenset({"tenant_owner"})
    assert "support:inbox:write" in authenticated.principal.scopes
    assert "automation:manage" in authenticated.principal.scopes
    assert "customers:read" in authenticated.principal.scopes
    with pytest.raises(PermissionError):
        await service.authenticate(AuthenticateAdminCommand(login.session.token, "correlation-4"))


@pytest.mark.asyncio
async def test_admin_login_rejects_wrong_password() -> None:
    store = InMemoryIdentityStore()
    service = AdminSessionService(
        identity_store=store,
        password_hasher=ScryptPasswordHasher(),
        session_ttl_seconds=3600,
    )
    await service.bootstrap_admin(
        BootstrapAdminCommand("tenant-a", "owner", "a-secure-password", "Owner")
    )

    with pytest.raises(PermissionError, match="invalid username or password"):
        await service.login(LoginAdminCommand("tenant-a", "owner", "wrong", "correlation"))


@pytest.mark.asyncio
async def test_admin_login_is_locked_after_configured_failures() -> None:
    store = InMemoryIdentityStore()
    service = AdminSessionService(
        identity_store=store,
        password_hasher=ScryptPasswordHasher(),
        session_ttl_seconds=3600,
        login_max_attempts=2,
        login_window_seconds=60,
        login_lockout_seconds=120,
    )
    await service.bootstrap_admin(
        BootstrapAdminCommand("tenant-a", "owner", "a-secure-password", "Owner")
    )
    command = LoginAdminCommand("tenant-a", "owner", "wrong", "correlation", "source-a")

    with pytest.raises(PermissionError, match="invalid username or password"):
        await service.login(command)
    with pytest.raises(AdminLoginThrottledError):
        await service.login(command)
    with pytest.raises(AdminLoginThrottledError):
        await service.login(
            LoginAdminCommand("tenant-a", "owner", "a-secure-password", "correlation", "source-a")
        )


@pytest.mark.asyncio
async def test_password_change_revokes_sessions_and_requires_new_password() -> None:
    store = InMemoryIdentityStore()
    service = AdminSessionService(
        identity_store=store,
        password_hasher=ScryptPasswordHasher(),
        session_ttl_seconds=3600,
    )
    await service.bootstrap_admin(
        BootstrapAdminCommand("tenant-a", "owner", "a-secure-password", "Owner")
    )
    login = await service.login(
        LoginAdminCommand("tenant-a", "owner", "a-secure-password", "correlation-1")
    )

    await service.change_password(
        ChangeAdminPasswordCommand(
            session_token=login.session.token,
            current_password="a-secure-password",
            new_password="a-new-secure-password",
            correlation_id="correlation-2",
        )
    )

    with pytest.raises(PermissionError, match="invalid or expired"):
        await service.authenticate(AuthenticateAdminCommand(login.session.token, "correlation-3"))
    with pytest.raises(PermissionError, match="invalid username or password"):
        await service.login(
            LoginAdminCommand("tenant-a", "owner", "a-secure-password", "correlation-4")
        )
    new_login = await service.login(
        LoginAdminCommand("tenant-a", "owner", "a-new-secure-password", "correlation-5")
    )
    assert new_login.session.user.username == "owner"


@pytest.mark.asyncio
async def test_admin_can_list_and_selectively_revoke_own_sessions() -> None:
    store = InMemoryIdentityStore()
    service = AdminSessionService(
        identity_store=store,
        password_hasher=ScryptPasswordHasher(),
        session_ttl_seconds=3600,
    )
    await service.bootstrap_admin(
        BootstrapAdminCommand("tenant-a", "owner", "a-secure-password", "Owner")
    )
    first = await service.login(
        LoginAdminCommand("tenant-a", "owner", "a-secure-password", "correlation-1", "source-one")
    )
    second = await service.login(
        LoginAdminCommand("tenant-a", "owner", "a-secure-password", "correlation-2", "source-two")
    )
    principal = (
        await service.authenticate(AuthenticateAdminCommand(first.session.token, "correlation-3"))
    ).principal

    listed = await service.list_sessions(ListAdminSessionsQuery(principal, first.session.token))
    second_session = next(item for item in listed.items if not item.is_current)
    revoked = await service.revoke_managed_session(
        RevokeAdminSessionCommand(
            principal=principal,
            current_session_token=first.session.token,
            session_id=second_session.session_id,
            correlation_id="correlation-4",
        )
    )
    repeated = await service.revoke_managed_session(
        RevokeAdminSessionCommand(
            principal=principal,
            current_session_token=first.session.token,
            session_id=second_session.session_id,
            correlation_id="correlation-5",
        )
    )

    assert len(listed.items) == 2
    assert (
        next(item for item in listed.items if item.is_current).source_fingerprint_prefix
        == "source-one"
    )
    assert revoked.changed is True
    assert revoked.was_current is False
    assert repeated.changed is False
    with pytest.raises(PermissionError, match="invalid or expired"):
        await service.authenticate(AuthenticateAdminCommand(second.session.token, "correlation-6"))
