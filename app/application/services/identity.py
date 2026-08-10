from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe
from uuid import NAMESPACE_URL, uuid4, uuid5

from app.application.dto.identity import (
    AdminSessionView,
    AuthenticateAdminCommand,
    AuthenticateAdminResult,
    BootstrapAdminCommand,
    ChangeAdminPasswordCommand,
    CreateAdminUserCommand,
    ListAdminSessionsQuery,
    ListAdminSessionsResult,
    ListAdminUsersQuery,
    ListAdminUsersResult,
    LoginAdminCommand,
    LoginAdminResult,
    LogoutAdminCommand,
    ResetAdminUserPasswordCommand,
    RevokeAdminSessionCommand,
    RevokeAdminSessionResult,
    UpdateAdminUserCommand,
)
from app.domain.models import (
    AdminSession,
    AdminUser,
    AuthenticatedPrincipal,
    IssuedAdminSession,
)
from app.domain.ports import AdminIdentityStorePort, PasswordHasherPort
from app.domain.rules.rbac import ADMIN_ROLES, scopes_for_roles


class AdminUserConflictError(ValueError):
    pass


class AdminLoginThrottledError(PermissionError):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("too many login attempts")
        self.retry_after_seconds = max(retry_after_seconds, 1)


class AdminSessionService:
    def __init__(
        self,
        *,
        identity_store: AdminIdentityStorePort,
        password_hasher: PasswordHasherPort,
        session_ttl_seconds: int,
        login_max_attempts: int = 10,
        login_window_seconds: int = 900,
        login_lockout_seconds: int = 900,
    ) -> None:
        self._identity_store = identity_store
        self._password_hasher = password_hasher
        self._session_ttl = timedelta(seconds=session_ttl_seconds)
        self._login_max_attempts = login_max_attempts
        self._login_window_seconds = login_window_seconds
        self._login_lockout_seconds = login_lockout_seconds
        self._dummy_password_hash = password_hasher.hash_password(token_urlsafe(32))

    async def login(self, command: LoginAdminCommand) -> LoginAdminResult:
        username = command.username.strip().casefold()
        now = datetime.now(UTC)
        locked_until = await self._identity_store.get_login_lock(
            source_fingerprint=command.source_fingerprint,
            checked_at=now,
        )
        if locked_until is not None:
            raise AdminLoginThrottledError(int((locked_until - now).total_seconds()))

        user = await self._identity_store.get_user_by_username(
            tenant_id=command.tenant_id,
            username=username,
        )
        encoded_hash = user.password_hash if user is not None else self._dummy_password_hash
        password_matches = self._password_hasher.verify_password(command.password, encoded_hash)
        if user is None or user.status != "active" or not password_matches:
            locked_until = await self._identity_store.record_login_failure(
                source_fingerprint=command.source_fingerprint,
                tenant_id=user.tenant_id if user is not None else "__authentication__",
                username=username,
                correlation_id=command.correlation_id,
                occurred_at=now,
                max_attempts=self._login_max_attempts,
                window_seconds=self._login_window_seconds,
                lockout_seconds=self._login_lockout_seconds,
            )
            if locked_until is not None:
                raise AdminLoginThrottledError(int((locked_until - now).total_seconds()))
            raise PermissionError("invalid username or password")

        await self._identity_store.clear_login_failures(
            source_fingerprint=command.source_fingerprint,
            tenant_id=user.tenant_id,
            user_id=user.user_id,
            correlation_id=command.correlation_id,
            cleared_at=now,
        )
        token = token_urlsafe(48)
        session = AdminSession(
            session_id=str(uuid4()),
            tenant_id=user.tenant_id,
            user_id=user.user_id,
            token_hash=_token_hash(token),
            expires_at=now + self._session_ttl,
            created_at=now,
            last_seen_at=now,
            source_fingerprint=command.source_fingerprint,
            authentication_method="local",
        )
        await self._identity_store.create_session(session)
        return LoginAdminResult(IssuedAdminSession(token, session.expires_at, user))

    async def authenticate(self, command: AuthenticateAdminCommand) -> AuthenticateAdminResult:
        if not command.session_token:
            raise PermissionError("administrative session is required")
        user = await self._identity_store.get_user_for_session(
            token_hash=_token_hash(command.session_token)
        )
        if user is None or user.status != "active":
            raise PermissionError("administrative session is invalid or expired")
        return AuthenticateAdminResult(
            principal=AuthenticatedPrincipal(
                subject_id=user.user_id,
                tenant_id=user.tenant_id,
                roles=user.roles,
                scopes=user.scopes,
                authentication_method=user.authentication_method,
                authenticated_at=datetime.now(UTC),
                correlation_id=command.correlation_id,
                platform_roles=user.platform_roles,
            ),
            user=user,
        )

    async def logout(self, command: LogoutAdminCommand) -> bool:
        if not command.session_token:
            return True
        return await self._identity_store.revoke_session(
            token_hash=_token_hash(command.session_token),
            revoked_at=datetime.now(UTC),
        )

    async def list_sessions(self, query: ListAdminSessionsQuery) -> ListAdminSessionsResult:
        current_token_hash = _token_hash(query.current_session_token)
        sessions = await self._identity_store.list_user_sessions(
            tenant_id=query.principal.tenant_id,
            user_id=query.principal.subject_id,
        )
        return ListAdminSessionsResult(
            items=tuple(
                AdminSessionView(
                    session_id=session.session_id,
                    created_at=session.created_at,
                    last_seen_at=session.last_seen_at,
                    expires_at=session.expires_at,
                    revoked_at=session.revoked_at,
                    source_fingerprint_prefix=session.source_fingerprint[:12],
                    is_current=session.token_hash == current_token_hash,
                )
                for session in sessions
            )
        )

    async def revoke_managed_session(
        self, command: RevokeAdminSessionCommand
    ) -> RevokeAdminSessionResult:
        session_id = command.session_id.strip()
        if not session_id:
            raise ValueError("session_id is required")
        sessions = await self._identity_store.list_user_sessions(
            tenant_id=command.principal.tenant_id,
            user_id=command.principal.subject_id,
        )
        target = next((session for session in sessions if session.session_id == session_id), None)
        if target is None:
            raise ValueError("administrative session was not found")
        was_current = target.token_hash == _token_hash(command.current_session_token)
        changed = await self._identity_store.revoke_user_session(
            tenant_id=command.principal.tenant_id,
            user_id=command.principal.subject_id,
            session_id=session_id,
            actor_subject_id=command.principal.subject_id,
            correlation_id=command.correlation_id,
            revoked_at=datetime.now(UTC),
        )
        if changed is None:
            raise ValueError("administrative session was not found")
        return RevokeAdminSessionResult(
            session_id=session_id,
            revoked=True,
            changed=changed,
            was_current=was_current,
        )

    async def change_password(self, command: ChangeAdminPasswordCommand) -> None:
        if len(command.new_password) < 12:
            raise ValueError("new password must contain at least 12 characters")
        if command.current_password == command.new_password:
            raise ValueError("new password must differ from the current password")
        authenticated = await self.authenticate(
            AuthenticateAdminCommand(command.session_token, command.correlation_id)
        )
        user = authenticated.user
        if not self._password_hasher.verify_password(command.current_password, user.password_hash):
            raise PermissionError("current password is invalid")
        changed = await self._identity_store.change_password_and_revoke_sessions(
            tenant_id=user.tenant_id,
            user_id=user.user_id,
            expected_password_hash=user.password_hash,
            new_password_hash=self._password_hasher.hash_password(command.new_password),
            correlation_id=command.correlation_id,
            changed_at=datetime.now(UTC),
        )
        if not changed:
            raise PermissionError("password changed concurrently; sign in again")

    async def bootstrap_admin(self, command: BootstrapAdminCommand) -> AdminUser:
        if not command.username.strip() or len(command.password) < 12:
            raise ValueError("bootstrap admin requires username and a 12+ character password")
        now = datetime.now(UTC)
        roles = frozenset({"tenant_owner"})
        user = AdminUser(
            user_id=str(uuid4()),
            tenant_id=command.tenant_id,
            username=command.username.strip().casefold(),
            display_name=command.display_name.strip() or "Tenant Owner",
            password_hash=self._password_hasher.hash_password(command.password),
            roles=roles,
            scopes=scopes_for_roles(roles),
            status="active",
            created_at=now,
            updated_at=now,
        )
        return await self._identity_store.create_user_if_absent(user)


def _token_hash(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


class AdminUserManagementService:
    def __init__(
        self,
        *,
        identity_store: AdminIdentityStorePort,
        password_hasher: PasswordHasherPort,
    ) -> None:
        self._identity_store = identity_store
        self._password_hasher = password_hasher

    async def list_users(self, query: ListAdminUsersQuery) -> ListAdminUsersResult:
        _require_user_management(query.principal)
        users = await self._identity_store.list_users(tenant_id=query.principal.tenant_id)
        return ListAdminUsersResult(tuple(users))

    async def create_user(self, command: CreateAdminUserCommand) -> AdminUser:
        _require_user_management(command.principal)
        username = command.username.strip().casefold()
        display_name = command.display_name.strip()
        roles = _validate_roles(command.roles)
        _validate_password(command.password)
        if not username or len(username) > 200:
            raise ValueError("username must contain between 1 and 200 characters")
        if not display_name or len(display_name) > 200:
            raise ValueError("display name must contain between 1 and 200 characters")
        existing = await self._identity_store.get_user_by_username(
            tenant_id=command.principal.tenant_id,
            username=username,
        )
        if existing is not None:
            if (
                existing.display_name == display_name
                and existing.roles == roles
                and existing.status == "active"
                and self._password_hasher.verify_password(command.password, existing.password_hash)
            ):
                return existing
            raise AdminUserConflictError("username already exists with different settings")
        now = datetime.now(UTC)
        user = AdminUser(
            user_id=str(
                uuid5(
                    NAMESPACE_URL,
                    f"admin-user:{command.principal.tenant_id}:{username}",
                )
            ),
            tenant_id=command.principal.tenant_id,
            username=username,
            display_name=display_name,
            password_hash=self._password_hasher.hash_password(command.password),
            roles=roles,
            scopes=scopes_for_roles(roles),
            status="active",
            created_at=now,
            updated_at=now,
        )
        return await self._identity_store.create_managed_user(
            user,
            actor_subject_id=command.principal.subject_id,
            correlation_id=command.correlation_id,
        )

    async def update_user(self, command: UpdateAdminUserCommand) -> AdminUser:
        _require_user_management(command.principal)
        roles = _validate_roles(command.roles)
        status = command.status.strip().casefold()
        display_name = command.display_name.strip()
        if status not in {"active", "disabled"}:
            raise ValueError("administrator status must be active or disabled")
        if not display_name or len(display_name) > 200:
            raise ValueError("display name must contain between 1 and 200 characters")
        if command.user_id == command.principal.subject_id and status != "active":
            raise AdminUserConflictError("you cannot disable your own active account")
        updated = await self._identity_store.update_managed_user(
            tenant_id=command.principal.tenant_id,
            user_id=command.user_id,
            display_name=display_name,
            roles=roles,
            scopes=scopes_for_roles(roles),
            status=status,
            actor_subject_id=command.principal.subject_id,
            correlation_id=command.correlation_id,
            changed_at=datetime.now(UTC),
        )
        if updated is None:
            raise LookupError("administrator user was not found or is the last active owner")
        return updated

    async def reset_password(self, command: ResetAdminUserPasswordCommand) -> AdminUser:
        _require_user_management(command.principal)
        _validate_password(command.new_password)
        user = await self._identity_store.get_user_by_id(
            tenant_id=command.principal.tenant_id,
            user_id=command.user_id,
        )
        if user is None:
            raise LookupError("administrator user was not found")
        if self._password_hasher.verify_password(command.new_password, user.password_hash):
            return user
        changed = await self._identity_store.reset_managed_password(
            tenant_id=user.tenant_id,
            user_id=user.user_id,
            expected_password_hash=user.password_hash,
            new_password_hash=self._password_hasher.hash_password(command.new_password),
            actor_subject_id=command.principal.subject_id,
            correlation_id=command.correlation_id,
            changed_at=datetime.now(UTC),
        )
        if not changed:
            raise AdminUserConflictError("administrator password changed concurrently")
        refreshed = await self._identity_store.get_user_by_id(
            tenant_id=user.tenant_id,
            user_id=user.user_id,
        )
        if refreshed is None:
            raise LookupError("administrator user was not found")
        return refreshed


def _require_user_management(principal: AuthenticatedPrincipal) -> None:
    if "users:manage" not in principal.scopes:
        raise PermissionError("administrator user management permission is required")


def _validate_roles(roles: frozenset[str]) -> frozenset[str]:
    if not roles or not roles.issubset(ADMIN_ROLES):
        raise ValueError("one or more administrator roles are invalid")
    return roles


def _validate_password(password: str) -> None:
    if len(password) < 12 or len(password) > 200:
        raise ValueError("administrator password must contain between 12 and 200 characters")
