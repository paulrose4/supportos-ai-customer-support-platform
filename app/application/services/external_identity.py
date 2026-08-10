from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe
from uuid import uuid4

from app.application.dto.identity import (
    CompleteExternalLoginCommand,
    CompleteExternalLoginResult,
    ListWorkspacesQuery,
    ListWorkspacesResult,
    SelectWorkspaceCommand,
    SelectWorkspaceResult,
    StartExternalLoginCommand,
    StartExternalLoginResult,
)
from app.domain.models import (
    AdminSession,
    IssuedAdminSession,
    LoginCompletionGrant,
    OAuthLoginState,
)
from app.domain.ports import ExternalIdentityProviderPort, WorkforceIdentityStorePort


class ExternalLoginConfigurationError(RuntimeError):
    pass


class ExternalLoginAccessDeniedError(PermissionError):
    pass


class ExternalLoginStateError(PermissionError):
    pass


class ExternalIdentityService:
    def __init__(
        self,
        *,
        providers: tuple[ExternalIdentityProviderPort, ...],
        identity_store: WorkforceIdentityStorePort,
        session_ttl_seconds: int,
        login_state_ttl_seconds: int = 300,
        completion_grant_ttl_seconds: int = 300,
    ) -> None:
        self._providers = {provider.provider_name: provider for provider in providers}
        self._identity_store = identity_store
        self._session_ttl = timedelta(seconds=session_ttl_seconds)
        self._login_state_ttl = timedelta(seconds=login_state_ttl_seconds)
        self._completion_grant_ttl = timedelta(seconds=completion_grant_ttl_seconds)

    @property
    def enabled_providers(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))

    async def start_login(self, command: StartExternalLoginCommand) -> StartExternalLoginResult:
        provider = self._provider(command.provider)
        return_path = _validated_return_path(command.return_path)
        raw_state = token_urlsafe(48)
        now = datetime.now(UTC)
        await self._identity_store.save_login_state(
            OAuthLoginState(
                state_id=str(uuid4()),
                provider=provider.provider_name,
                state_hash=_token_hash(raw_state),
                return_path=return_path,
                expires_at=now + self._login_state_ttl,
                created_at=now,
            )
        )
        return StartExternalLoginResult(
            authorization_url=provider.build_authorization_url(
                state=raw_state,
                redirect_uri=command.redirect_uri,
            )
        )

    async def complete_login(
        self, command: CompleteExternalLoginCommand
    ) -> CompleteExternalLoginResult:
        provider = self._provider(command.provider)
        now = datetime.now(UTC)
        login_state = await self._identity_store.consume_login_state(
            provider=provider.provider_name,
            state_hash=_token_hash(command.state),
            consumed_at=now,
        )
        if login_state is None:
            raise ExternalLoginStateError("external login state is invalid, expired, or replayed")

        identity = await provider.exchange_authorization_code(
            code=command.code,
            redirect_uri=command.redirect_uri,
        )
        if identity.provider != provider.provider_name:
            raise ExternalLoginAccessDeniedError("external identity provider mismatch")
        user = await self._identity_store.upsert_external_user(
            identity=identity,
            correlation_id=command.correlation_id,
            occurred_at=now,
        )
        memberships = tuple(
            await self._identity_store.list_active_memberships(user_id=user.user_id)
        )
        if not memberships:
            raise ExternalLoginAccessDeniedError(
                "identity verified but no active workspace membership is assigned"
            )
        if len(memberships) == 1:
            token, session = self._new_session(
                user_id=user.user_id,
                tenant_id=memberships[0].tenant_id,
                authentication_method=provider.provider_name,
                source_fingerprint=command.source_fingerprint,
                now=now,
            )
            workspace_user = await self._identity_store.create_session_for_workspace(session)
            if workspace_user is None:
                raise ExternalLoginAccessDeniedError("workspace membership is no longer active")
            return CompleteExternalLoginResult(
                session=IssuedAdminSession(token, session.expires_at, workspace_user),
                completion_token=None,
                return_path=login_state.return_path,
                memberships=memberships,
            )

        completion_token = token_urlsafe(48)
        await self._identity_store.save_login_completion_grant(
            LoginCompletionGrant(
                grant_id=str(uuid4()),
                user_id=user.user_id,
                token_hash=_token_hash(completion_token),
                expires_at=now + self._completion_grant_ttl,
                created_at=now,
            )
        )
        return CompleteExternalLoginResult(
            session=None,
            completion_token=completion_token,
            return_path=login_state.return_path,
            memberships=memberships,
        )

    async def list_workspaces(self, query: ListWorkspacesQuery) -> ListWorkspacesResult:
        if query.principal is not None:
            memberships = await self._identity_store.list_active_memberships(
                user_id=query.principal.subject_id
            )
            return ListWorkspacesResult(tuple(memberships), query.principal.tenant_id)
        if not query.completion_token:
            raise ExternalLoginAccessDeniedError("an authenticated or pending login is required")
        grant = await self._identity_store.get_login_completion_grant(
            token_hash=_token_hash(query.completion_token),
            checked_at=datetime.now(UTC),
        )
        if grant is None:
            raise ExternalLoginAccessDeniedError("pending login is invalid or expired")
        memberships = await self._identity_store.list_active_memberships(user_id=grant.user_id)
        return ListWorkspacesResult(tuple(memberships), None)

    async def select_workspace(self, command: SelectWorkspaceCommand) -> SelectWorkspaceResult:
        tenant_id = command.tenant_id.strip()
        if not tenant_id:
            raise ValueError("tenant_id is required")
        now = datetime.now(UTC)
        if command.principal is not None and command.current_session_token:
            token, replacement = self._new_session(
                user_id=command.principal.subject_id,
                tenant_id=tenant_id,
                authentication_method=command.principal.authentication_method,
                source_fingerprint=command.source_fingerprint,
                now=now,
            )
            user = await self._identity_store.rotate_session_workspace(
                current_token_hash=_token_hash(command.current_session_token),
                tenant_id=tenant_id,
                replacement=replacement,
                rotated_at=now,
            )
        elif command.completion_token:
            grant = await self._identity_store.get_login_completion_grant(
                token_hash=_token_hash(command.completion_token),
                checked_at=now,
            )
            if grant is None:
                raise ExternalLoginAccessDeniedError("pending login is invalid or expired")
            token, replacement = self._new_session(
                user_id=grant.user_id,
                tenant_id=tenant_id,
                authentication_method=grant.authentication_method,
                source_fingerprint=command.source_fingerprint,
                now=now,
            )
            user = await self._identity_store.create_session_from_login_grant(
                grant_token_hash=_token_hash(command.completion_token),
                tenant_id=tenant_id,
                session=replacement,
                consumed_at=now,
            )
        else:
            raise ExternalLoginAccessDeniedError("an authenticated or pending login is required")
        if user is None:
            raise ExternalLoginAccessDeniedError("workspace membership is invalid or inactive")
        return SelectWorkspaceResult(IssuedAdminSession(token, replacement.expires_at, user))

    def _provider(self, provider_name: str) -> ExternalIdentityProviderPort:
        provider = self._providers.get(provider_name.strip().casefold())
        if provider is None:
            raise ExternalLoginConfigurationError("external identity provider is not enabled")
        return provider

    def _new_session(
        self,
        *,
        user_id: str,
        tenant_id: str,
        authentication_method: str,
        source_fingerprint: str,
        now: datetime,
    ) -> tuple[str, AdminSession]:
        token = token_urlsafe(48)
        return token, AdminSession(
            session_id=str(uuid4()),
            tenant_id=tenant_id,
            user_id=user_id,
            token_hash=_token_hash(token),
            expires_at=now + self._session_ttl,
            created_at=now,
            last_seen_at=now,
            source_fingerprint=source_fingerprint,
            authentication_method=authentication_method,
        )


def _validated_return_path(value: str) -> str:
    path = value.strip() or "/"
    if not path.startswith("/") or path.startswith("//") or "\\" in path:
        raise ValueError("return_path must be an application-relative path")
    return path


def _token_hash(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()
