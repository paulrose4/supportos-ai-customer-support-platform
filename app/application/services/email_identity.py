import re
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe
from uuid import uuid4

from app.application.dto.identity import (
    ChangeEmailPasswordCommand,
    CreateTenantInvitationCommand,
    CreateTenantInvitationResult,
    EmailAuthenticationResult,
    EmailLoginCommand,
    InvitationPreviewResult,
    ListTenantInvitationsQuery,
    ListTenantInvitationsResult,
    RegisterWithInvitationCommand,
    RequestPasswordResetCommand,
    ResetEmailPasswordCommand,
    RevokeTenantInvitationCommand,
)
from app.domain.models import (
    AdminSession,
    AuthenticatedPrincipal,
    IssuedAdminSession,
    LoginCompletionGrant,
    PasswordResetToken,
    TenantInvitation,
    TenantMembership,
)
from app.domain.ports import EmailIdentityStorePort, PasswordHasherPort, TransactionalEmailPort
from app.domain.rules.rbac import ADMIN_ROLES

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class EmailLoginThrottledError(PermissionError):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("too many login attempts")
        self.retry_after_seconds = max(retry_after_seconds, 1)


class InvitationAccessError(PermissionError):
    pass


class InvitationConflictError(ValueError):
    pass


class EmailIdentityService:
    def __init__(
        self,
        *,
        store: EmailIdentityStorePort,
        password_hasher: PasswordHasherPort,
        session_ttl_seconds: int,
        completion_grant_ttl_seconds: int,
        password_reset_ttl_seconds: int,
        public_base_url: str,
        transactional_email: TransactionalEmailPort | None = None,
        login_max_attempts: int = 10,
        login_window_seconds: int = 900,
        login_lockout_seconds: int = 900,
    ) -> None:
        self._store = store
        self._password_hasher = password_hasher
        self._session_ttl = timedelta(seconds=session_ttl_seconds)
        self._completion_grant_ttl = timedelta(seconds=completion_grant_ttl_seconds)
        self._password_reset_ttl = timedelta(seconds=password_reset_ttl_seconds)
        self._public_base_url = public_base_url.rstrip("/")
        self._transactional_email = transactional_email
        self._login_max_attempts = login_max_attempts
        self._login_window_seconds = login_window_seconds
        self._login_lockout_seconds = login_lockout_seconds
        self._dummy_password_hash = password_hasher.hash_password(token_urlsafe(32))

    async def login(self, command: EmailLoginCommand) -> EmailAuthenticationResult:
        normalized_email = normalize_email(command.email)
        email_hash = _token_hash(normalized_email)
        now = datetime.now(UTC)
        locked_until = await self._store.get_login_lock(
            source_fingerprint=command.source_fingerprint,
            email_hash=email_hash,
            checked_at=now,
        )
        if locked_until is not None:
            raise EmailLoginThrottledError(int((locked_until - now).total_seconds()))

        account = await self._store.get_login_account(normalized_email=normalized_email)
        encoded_hash = account.password_hash if account is not None else self._dummy_password_hash
        password_matches = self._password_hasher.verify_password(command.password, encoded_hash)
        if (
            account is None
            or account.user.status != "active"
            or account.identity.status != "active"
            or account.identity.verified_at is None
            or not password_matches
        ):
            locked_until = await self._store.record_login_failure(
                source_fingerprint=command.source_fingerprint,
                email_hash=email_hash,
                correlation_id=command.correlation_id,
                occurred_at=now,
                max_attempts=self._login_max_attempts,
                window_seconds=self._login_window_seconds,
                lockout_seconds=self._login_lockout_seconds,
            )
            if locked_until is not None:
                raise EmailLoginThrottledError(int((locked_until - now).total_seconds()))
            raise PermissionError("invalid email or password")

        memberships = await self._store.list_active_memberships(user_id=account.user.user_id)
        if not memberships:
            raise InvitationAccessError("account has no active workspace membership")
        await self._store.clear_login_failures(
            source_fingerprint=command.source_fingerprint,
            email_hash=email_hash,
            user_id=account.user.user_id,
            correlation_id=command.correlation_id,
            cleared_at=now,
        )
        return await self._issue_access(
            user_id=account.user.user_id,
            memberships=memberships,
            source_fingerprint=command.source_fingerprint,
            now=now,
        )

    async def register(self, command: RegisterWithInvitationCommand) -> EmailAuthenticationResult:
        _validate_password(command.password)
        display_name = command.display_name.strip()
        if not display_name or len(display_name) > 200:
            raise ValueError("display_name must contain between 1 and 200 characters")
        token_hash = _token_hash(command.invitation_token)
        now = datetime.now(UTC)
        invitation = await self._store.get_invitation_by_token(
            token_hash=token_hash,
            checked_at=now,
        )
        if invitation is None:
            raise InvitationAccessError("invitation is invalid, expired, or already used")

        account = await self._store.get_login_account(normalized_email=invitation.normalized_email)
        existing_user_id: str | None = None
        password_hash: str | None
        if account is not None:
            if not self._password_hasher.verify_password(command.password, account.password_hash):
                raise PermissionError("an account already exists; use its current password")
            existing_user_id = account.user.user_id
            password_hash = None
        else:
            password_hash = self._password_hasher.hash_password(command.password)

        registered = await self._store.redeem_invitation(
            token_hash=token_hash,
            display_name=display_name,
            password_hash=password_hash,
            existing_user_id=existing_user_id,
            correlation_id=command.correlation_id,
            redeemed_at=now,
        )
        if registered is None:
            raise InvitationAccessError("invitation is invalid, expired, or already used")
        memberships = await self._store.list_active_memberships(user_id=registered.user.user_id)
        return await self._issue_access(
            user_id=registered.user.user_id,
            memberships=memberships,
            source_fingerprint=command.source_fingerprint,
            now=now,
        )

    async def preview_invitation(self, invitation_token: str) -> InvitationPreviewResult:
        invitation = await self._store.get_invitation_by_token(
            token_hash=_token_hash(invitation_token),
            checked_at=datetime.now(UTC),
        )
        if invitation is None:
            raise InvitationAccessError("invitation is invalid, expired, or already used")
        return InvitationPreviewResult(
            tenant_name=invitation.tenant_name,
            email=invitation.display_email,
            roles=tuple(sorted(invitation.roles)),
            expires_at=invitation.expires_at,
        )

    async def request_password_reset(self, command: RequestPasswordResetCommand) -> None:
        if self._transactional_email is None:
            return
        try:
            normalized_email = normalize_email(command.email)
        except ValueError:
            return
        account = await self._store.get_login_account(normalized_email=normalized_email)
        if (
            account is None
            or account.user.status != "active"
            or account.identity.status != "active"
            or account.identity.verified_at is None
        ):
            return
        raw_token = token_urlsafe(48)
        now = datetime.now(UTC)
        reset = PasswordResetToken(
            reset_id=str(uuid4()),
            user_id=account.user.user_id,
            token_hash=_token_hash(raw_token),
            expires_at=now + self._password_reset_ttl,
            created_at=now,
        )
        await self._store.create_password_reset(reset)
        try:
            await self._transactional_email.send_password_reset(
                recipient=account.identity.display_email,
                display_name=account.user.display_name,
                reset_url=f"{self._public_base_url}/#reset={raw_token}",
                expires_at=reset.expires_at,
            )
        except (ConnectionError, OSError, TimeoutError):
            return

    async def reset_password(self, command: ResetEmailPasswordCommand) -> None:
        _validate_password(command.new_password)
        changed = await self._store.reset_password(
            token_hash=_token_hash(command.reset_token),
            new_password_hash=self._password_hasher.hash_password(command.new_password),
            correlation_id=command.correlation_id,
            changed_at=datetime.now(UTC),
        )
        if not changed:
            raise InvitationAccessError("password reset link is invalid, expired, or already used")

    async def change_password(self, command: ChangeEmailPasswordCommand) -> None:
        _validate_password(command.new_password)
        account = await self._store.get_login_account_for_session(
            token_hash=_token_hash(command.session_token)
        )
        if account is None or not self._password_hasher.verify_password(
            command.current_password, account.password_hash
        ):
            raise PermissionError("current password is invalid")
        changed = await self._store.change_password_from_session(
            session_token_hash=_token_hash(command.session_token),
            expected_password_hash=account.password_hash,
            new_password_hash=self._password_hasher.hash_password(command.new_password),
            correlation_id=command.correlation_id,
            changed_at=datetime.now(UTC),
        )
        if not changed:
            raise PermissionError("password changed concurrently; sign in again")

    async def bootstrap_email_identity(
        self,
        *,
        user_id: str,
        email: str,
        password: str,
    ) -> None:
        normalized_email = normalize_email(email)
        _validate_password(password)
        await self._store.bootstrap_email_identity(
            user_id=user_id,
            normalized_email=normalized_email,
            display_email=email.strip(),
            password_hash=self._password_hasher.hash_password(password),
            occurred_at=datetime.now(UTC),
        )

    async def _issue_access(
        self,
        *,
        user_id: str,
        memberships: list[TenantMembership],
        source_fingerprint: str,
        now: datetime,
    ) -> EmailAuthenticationResult:
        if not memberships:
            raise InvitationAccessError("account has no active workspace membership")
        if len(memberships) == 1:
            raw_token, session = self._new_session(
                user_id=user_id,
                tenant_id=memberships[0].tenant_id,
                source_fingerprint=source_fingerprint,
                now=now,
            )
            workspace_user = await self._store.create_session_for_workspace(session)
            if workspace_user is None:
                raise InvitationAccessError("workspace membership is no longer active")
            return EmailAuthenticationResult(
                session=IssuedAdminSession(raw_token, session.expires_at, workspace_user),
                completion_token=None,
            )

        completion_token = token_urlsafe(48)
        await self._store.save_login_completion_grant(
            LoginCompletionGrant(
                grant_id=str(uuid4()),
                user_id=user_id,
                token_hash=_token_hash(completion_token),
                expires_at=now + self._completion_grant_ttl,
                created_at=now,
                authentication_method="email_password",
            )
        )
        return EmailAuthenticationResult(session=None, completion_token=completion_token)

    def _new_session(
        self,
        *,
        user_id: str,
        tenant_id: str,
        source_fingerprint: str,
        now: datetime,
    ) -> tuple[str, AdminSession]:
        raw_token = token_urlsafe(48)
        return raw_token, AdminSession(
            session_id=str(uuid4()),
            tenant_id=tenant_id,
            user_id=user_id,
            token_hash=_token_hash(raw_token),
            expires_at=now + self._session_ttl,
            created_at=now,
            last_seen_at=now,
            source_fingerprint=source_fingerprint,
            authentication_method="email_password",
        )


class InvitationManagementService:
    def __init__(
        self,
        *,
        store: EmailIdentityStorePort,
        public_base_url: str,
        transactional_email: TransactionalEmailPort | None = None,
    ) -> None:
        self._store = store
        self._public_base_url = public_base_url.rstrip("/")
        self._transactional_email = transactional_email

    async def create_invitation(
        self, command: CreateTenantInvitationCommand
    ) -> CreateTenantInvitationResult:
        _require_invitation_management(command.principal, command.tenant_id)
        normalized_email = normalize_email(command.email)
        roles = command.roles
        if not roles or not roles.issubset(ADMIN_ROLES):
            raise ValueError("one or more tenant roles are invalid")
        if not 1 <= command.expires_in_hours <= 168:
            raise ValueError("invitation expiry must be between 1 and 168 hours")
        existing = await self._store.get_login_account(normalized_email=normalized_email)
        if existing is not None:
            memberships = await self._store.list_active_memberships(user_id=existing.user.user_id)
            if any(item.tenant_id == command.tenant_id for item in memberships):
                raise InvitationConflictError("this email already belongs to the workspace")

        raw_token = token_urlsafe(48)
        now = datetime.now(UTC)
        invitation = await self._store.create_invitation(
            TenantInvitation(
                invitation_id=str(uuid4()),
                tenant_id=command.tenant_id,
                tenant_name=command.tenant_id,
                normalized_email=normalized_email,
                display_email=command.email.strip(),
                roles=roles,
                token_hash=_token_hash(raw_token),
                status="pending",
                expires_at=now + timedelta(hours=command.expires_in_hours),
                created_by=command.principal.subject_id,
                created_at=now,
            ),
            correlation_id=command.correlation_id,
        )
        invitation_url = f"{self._public_base_url}/#invite={raw_token}"
        email_sent = False
        if self._transactional_email is not None:
            try:
                await self._transactional_email.send_invitation(
                    recipient=invitation.display_email,
                    inviter_name=command.principal.subject_id,
                    tenant_name=invitation.tenant_name,
                    invitation_url=invitation_url,
                    expires_at=invitation.expires_at,
                )
                email_sent = True
            except (ConnectionError, OSError, TimeoutError):
                email_sent = False
        return CreateTenantInvitationResult(invitation, invitation_url, email_sent)

    async def list_invitations(
        self, query: ListTenantInvitationsQuery
    ) -> ListTenantInvitationsResult:
        _require_invitation_management(query.principal, query.tenant_id)
        return ListTenantInvitationsResult(
            tuple(await self._store.list_invitations(tenant_id=query.tenant_id))
        )

    async def revoke_invitation(self, command: RevokeTenantInvitationCommand) -> TenantInvitation:
        _require_invitation_management(command.principal, command.tenant_id)
        invitation = await self._store.revoke_invitation(
            tenant_id=command.tenant_id,
            invitation_id=command.invitation_id,
            actor_subject_id=command.principal.subject_id,
            correlation_id=command.correlation_id,
            revoked_at=datetime.now(UTC),
        )
        if invitation is None:
            raise LookupError("invitation was not found")
        return invitation


def normalize_email(value: str) -> str:
    email = value.strip()
    if len(email) > 320 or not _EMAIL_PATTERN.fullmatch(email):
        raise ValueError("a valid email address is required")
    local, domain = email.rsplit("@", 1)
    try:
        normalized_domain = domain.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("a valid email address is required") from exc
    return f"{local.casefold()}@{normalized_domain.casefold()}"


def _require_invitation_management(principal: AuthenticatedPrincipal, tenant_id: str) -> None:
    platform_operator = bool(
        principal.platform_roles.intersection({"platform_owner", "platform_operator"})
    )
    tenant_operator = principal.tenant_id == tenant_id and "users:manage" in principal.scopes
    if not platform_operator and not tenant_operator:
        raise PermissionError("workspace member management permission is required")


def _validate_password(password: str) -> None:
    if not 12 <= len(password) <= 200:
        raise ValueError("password must contain between 12 and 200 characters")


def _token_hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()
