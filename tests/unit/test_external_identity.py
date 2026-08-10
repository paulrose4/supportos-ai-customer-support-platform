import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from urllib.parse import parse_qs, urlparse

import pytest

from app.application.dto import (
    CompleteExternalLoginCommand,
    ListWorkspacesQuery,
    SelectWorkspaceCommand,
    StartExternalLoginCommand,
)
from app.application.services import (
    ExternalIdentityService,
    ExternalLoginAccessDeniedError,
    ExternalLoginStateError,
)
from app.domain.models import (
    AdminSession,
    AdminUser,
    AuthenticatedPrincipal,
    IdentityUser,
    LoginCompletionGrant,
    OAuthLoginState,
    TenantMembership,
    VerifiedExternalIdentity,
)
from app.domain.rules.rbac import scopes_for_roles


class FakeExternalProvider:
    provider_name = "dingtalk"

    def build_authorization_url(self, *, state: str, redirect_uri: str) -> str:
        return f"https://login.example.test/auth?state={state}&redirect_uri={redirect_uri}"

    async def exchange_authorization_code(
        self, *, code: str, redirect_uri: str
    ) -> VerifiedExternalIdentity:
        del code, redirect_uri
        return VerifiedExternalIdentity(
            provider="dingtalk",
            organization_id="corp-a",
            provider_subject_id="union-1",
            provider_user_id="open-1",
            display_name="Ding User",
        )


class InMemoryWorkforceIdentityStore:
    def __init__(self) -> None:
        now = datetime.now(UTC)
        self.user = IdentityUser("user-1", "Ding User", "active", now, now)
        self.states: dict[str, OAuthLoginState] = {}
        self.grants: dict[str, LoginCompletionGrant] = {}
        self.memberships: list[TenantMembership] = []
        self.sessions: dict[str, AdminSession] = {}
        self._lock = asyncio.Lock()

    def add_membership(self, tenant_id: str, name: str) -> None:
        now = datetime.now(UTC)
        roles = frozenset({"tenant_owner"})
        self.memberships.append(
            TenantMembership(
                membership_id=f"membership-{tenant_id}",
                tenant_id=tenant_id,
                tenant_name=name,
                user_id=self.user.user_id,
                roles=roles,
                scopes=scopes_for_roles(roles),
                status="active",
                created_at=now,
                updated_at=now,
            )
        )

    async def save_login_state(self, state: OAuthLoginState) -> None:
        self.states[state.state_hash] = state

    async def consume_login_state(
        self, *, provider: str, state_hash: str, consumed_at: datetime
    ) -> OAuthLoginState | None:
        async with self._lock:
            state = self.states.get(state_hash)
            if (
                state is None
                or state.provider != provider
                or state.consumed_at is not None
                or state.expires_at <= consumed_at
            ):
                return None
            self.states[state_hash] = replace(state, consumed_at=consumed_at)
            return state

    async def upsert_external_user(self, **kwargs) -> IdentityUser:  # type: ignore[no-untyped-def]
        del kwargs
        return self.user

    async def list_active_memberships(self, *, user_id: str) -> list[TenantMembership]:
        return [
            item for item in self.memberships if item.user_id == user_id and item.status == "active"
        ]

    async def get_workspace_user(self, *, user_id: str, tenant_id: str) -> AdminUser | None:
        membership = next(
            (
                item
                for item in self.memberships
                if item.user_id == user_id
                and item.tenant_id == tenant_id
                and item.status == "active"
            ),
            None,
        )
        if membership is None:
            return None
        now = datetime.now(UTC)
        return AdminUser(
            user_id=user_id,
            tenant_id=tenant_id,
            username="",
            display_name=self.user.display_name,
            password_hash="",
            roles=membership.roles,
            scopes=membership.scopes,
            status="active",
            created_at=now,
            updated_at=now,
            authentication_method="dingtalk",
        )

    async def save_login_completion_grant(self, grant: LoginCompletionGrant) -> None:
        self.grants[grant.token_hash] = grant

    async def consume_login_completion_grant(
        self, *, token_hash: str, consumed_at: datetime
    ) -> LoginCompletionGrant | None:
        async with self._lock:
            grant = await self.get_login_completion_grant(
                token_hash=token_hash, checked_at=consumed_at
            )
            if grant is None:
                return None
            self.grants[token_hash] = replace(grant, consumed_at=consumed_at)
            return grant

    async def get_login_completion_grant(
        self, *, token_hash: str, checked_at: datetime
    ) -> LoginCompletionGrant | None:
        grant = self.grants.get(token_hash)
        if grant is None or grant.consumed_at is not None or grant.expires_at <= checked_at:
            return None
        return grant

    async def create_session(self, session: AdminSession) -> None:
        self.sessions[session.token_hash] = session

    async def create_session_for_workspace(self, session: AdminSession) -> AdminUser | None:
        user = await self.get_workspace_user(user_id=session.user_id, tenant_id=session.tenant_id)
        if user is not None:
            self.sessions[session.token_hash] = session
        return user

    async def create_session_from_login_grant(
        self,
        *,
        grant_token_hash: str,
        tenant_id: str,
        session: AdminSession,
        consumed_at: datetime,
    ) -> AdminUser | None:
        async with self._lock:
            grant = await self.get_login_completion_grant(
                token_hash=grant_token_hash, checked_at=consumed_at
            )
            if grant is None:
                return None
            user = await self.get_workspace_user(user_id=grant.user_id, tenant_id=tenant_id)
            if user is None:
                return None
            self.grants[grant_token_hash] = replace(grant, consumed_at=consumed_at)
            self.sessions[session.token_hash] = session
            return user

    async def rotate_session_workspace(
        self,
        *,
        current_token_hash: str,
        tenant_id: str,
        replacement: AdminSession,
        rotated_at: datetime,
    ) -> AdminUser | None:
        current = self.sessions.get(current_token_hash)
        if current is None or current.revoked_at is not None:
            return None
        user = await self.get_workspace_user(user_id=current.user_id, tenant_id=tenant_id)
        if user is None:
            return None
        self.sessions[current_token_hash] = replace(current, revoked_at=rotated_at)
        self.sessions[replacement.token_hash] = replacement
        return user


def _service(store: InMemoryWorkforceIdentityStore) -> ExternalIdentityService:
    return ExternalIdentityService(
        providers=(FakeExternalProvider(),),
        identity_store=store,
        session_ttl_seconds=3600,
    )


async def _begin(service: ExternalIdentityService) -> str:
    result = await service.start_login(
        StartExternalLoginCommand("dingtalk", "https://app.test/callback", "/")
    )
    return parse_qs(urlparse(result.authorization_url).query)["state"][0]


def _complete(state: str) -> CompleteExternalLoginCommand:
    return CompleteExternalLoginCommand(
        provider="dingtalk",
        code="one-time-code",
        state=state,
        redirect_uri="https://app.test/callback",
        source_fingerprint="browser-a",
        correlation_id="correlation-a",
    )


@pytest.mark.asyncio
async def test_external_login_state_is_atomic_and_cannot_be_replayed() -> None:
    store = InMemoryWorkforceIdentityStore()
    store.add_membership("tenant-a", "Tenant A")
    service = _service(store)
    state = await _begin(service)

    results = await asyncio.gather(
        service.complete_login(_complete(state)),
        service.complete_login(_complete(state)),
        return_exceptions=True,
    )

    assert sum(not isinstance(item, Exception) for item in results) == 1
    assert sum(isinstance(item, ExternalLoginStateError) for item in results) == 1


@pytest.mark.asyncio
async def test_verified_identity_without_membership_is_denied() -> None:
    store = InMemoryWorkforceIdentityStore()
    service = _service(store)

    with pytest.raises(ExternalLoginAccessDeniedError, match="no active workspace"):
        await service.complete_login(_complete(await _begin(service)))


@pytest.mark.asyncio
async def test_multi_workspace_grant_is_one_time_and_tenant_scoped() -> None:
    store = InMemoryWorkforceIdentityStore()
    store.add_membership("tenant-a", "Tenant A")
    store.add_membership("tenant-b", "Tenant B")
    service = _service(store)
    login = await service.complete_login(_complete(await _begin(service)))

    assert login.session is None
    assert login.completion_token is not None
    listed = await service.list_workspaces(
        ListWorkspacesQuery(principal=None, completion_token=login.completion_token)
    )
    assert {item.tenant_id for item in listed.items} == {"tenant-a", "tenant-b"}

    selected = await service.select_workspace(
        SelectWorkspaceCommand(
            tenant_id="tenant-b",
            completion_token=login.completion_token,
            source_fingerprint="browser-a",
            correlation_id="correlation-a",
        )
    )
    assert selected.session.user.tenant_id == "tenant-b"
    with pytest.raises(ExternalLoginAccessDeniedError):
        await service.select_workspace(
            SelectWorkspaceCommand(
                tenant_id="tenant-a",
                completion_token=login.completion_token,
                source_fingerprint="browser-a",
                correlation_id="correlation-b",
            )
        )


@pytest.mark.asyncio
async def test_workspace_switch_rejects_nonmember_and_rotates_current_session() -> None:
    store = InMemoryWorkforceIdentityStore()
    store.add_membership("tenant-a", "Tenant A")
    service = _service(store)
    login = await service.complete_login(_complete(await _begin(service)))
    assert login.session is not None
    user = login.session.user
    authenticated = AuthenticatedPrincipal(
        subject_id=user.user_id,
        tenant_id=user.tenant_id,
        roles=user.roles,
        scopes=user.scopes,
        authentication_method="dingtalk",
        authenticated_at=datetime.now(UTC),
        correlation_id="correlation-a",
    )
    with pytest.raises(ExternalLoginAccessDeniedError):
        await service.select_workspace(
            SelectWorkspaceCommand(
                tenant_id="tenant-b",
                principal=authenticated,
                current_session_token=login.session.token,
                source_fingerprint="browser-a",
                correlation_id="correlation-b",
            )
        )

    store.add_membership("tenant-b", "Tenant B")
    switched = await service.select_workspace(
        SelectWorkspaceCommand(
            tenant_id="tenant-b",
            principal=authenticated,
            current_session_token=login.session.token,
            source_fingerprint="browser-a",
            correlation_id="correlation-c",
        )
    )
    old_hash = sha256(login.session.token.encode()).hexdigest()
    assert switched.session.user.tenant_id == "tenant-b"
    assert store.sessions[old_hash].revoked_at is not None
