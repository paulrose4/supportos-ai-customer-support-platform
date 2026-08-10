from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.application.dto import (
    CreateTenantInvitationCommand,
    EmailLoginCommand,
    RegisterWithInvitationCommand,
)
from app.application.services import (
    EmailIdentityService,
    InvitationAccessError,
    InvitationManagementService,
)
from app.application.services.email_identity import normalize_email
from app.domain.models import (
    AdminUser,
    AuthenticatedPrincipal,
    EmailIdentity,
    EmailLoginAccount,
    IdentityUser,
    TenantInvitation,
    TenantMembership,
)


class FakePasswordHasher:
    def hash_password(self, password: str) -> str:
        return f"hash:{password}"

    def verify_password(self, password: str, encoded_hash: str) -> bool:
        return encoded_hash == f"hash:{password}"


def _account() -> EmailLoginAccount:
    now = datetime.now(UTC)
    user = IdentityUser(
        user_id="user-a",
        display_name="Owner",
        status="active",
        created_at=now,
        updated_at=now,
        primary_email="owner@example.com",
    )
    return EmailLoginAccount(
        user=user,
        identity=EmailIdentity(
            identity_id="email-a",
            user_id=user.user_id,
            normalized_email="owner@example.com",
            display_email="Owner@Example.com",
            status="active",
            verified_at=now,
            created_at=now,
            updated_at=now,
        ),
        password_hash="hash:correct-password",
        password_version=1,
    )


def _membership(tenant_id: str = "tenant-a") -> TenantMembership:
    now = datetime.now(UTC)
    return TenantMembership(
        membership_id=f"membership-{tenant_id}",
        tenant_id=tenant_id,
        tenant_name=tenant_id,
        user_id="user-a",
        roles=frozenset({"tenant_owner"}),
        scopes=frozenset({"users:manage"}),
        status="active",
        created_at=now,
        updated_at=now,
    )


def _workspace_user(tenant_id: str = "tenant-a") -> AdminUser:
    now = datetime.now(UTC)
    return AdminUser(
        user_id="user-a",
        tenant_id=tenant_id,
        username="Owner@Example.com",
        display_name="Owner",
        password_hash="",
        roles=frozenset({"tenant_owner"}),
        scopes=frozenset({"users:manage"}),
        status="active",
        created_at=now,
        updated_at=now,
        authentication_method="email_password",
    )


def _store() -> SimpleNamespace:
    return SimpleNamespace(
        get_login_lock=AsyncMock(return_value=None),
        get_login_account=AsyncMock(return_value=_account()),
        record_login_failure=AsyncMock(return_value=None),
        clear_login_failures=AsyncMock(return_value=None),
        list_active_memberships=AsyncMock(return_value=[_membership()]),
        create_session_for_workspace=AsyncMock(return_value=_workspace_user()),
        save_login_completion_grant=AsyncMock(return_value=None),
        get_invitation_by_token=AsyncMock(return_value=None),
        redeem_invitation=AsyncMock(return_value=_account()),
        create_invitation=AsyncMock(),
        list_invitations=AsyncMock(return_value=[]),
        revoke_invitation=AsyncMock(return_value=None),
        create_password_reset=AsyncMock(return_value=None),
        reset_password=AsyncMock(return_value=True),
        get_login_account_for_session=AsyncMock(return_value=_account()),
        change_password_from_session=AsyncMock(return_value=True),
        bootstrap_email_identity=AsyncMock(return_value=_account()),
    )


def _service(store: SimpleNamespace) -> EmailIdentityService:
    return EmailIdentityService(
        store=store,
        password_hasher=FakePasswordHasher(),
        session_ttl_seconds=3600,
        completion_grant_ttl_seconds=300,
        password_reset_ttl_seconds=1800,
        public_base_url="https://support.example.com",
    )


@pytest.mark.asyncio
async def test_email_login_does_not_accept_tenant_from_the_caller() -> None:
    store = _store()
    result = await _service(store).login(
        EmailLoginCommand(
            email=" OWNER@EXAMPLE.COM ",
            password="correct-password",
            correlation_id="correlation-a",
            source_fingerprint="browser-a",
        )
    )

    assert result.session is not None
    assert result.session.user.tenant_id == "tenant-a"
    assert result.session.user.authentication_method == "email_password"
    store.get_login_account.assert_awaited_once_with(normalized_email="owner@example.com")


@pytest.mark.asyncio
async def test_email_login_uses_authentication_method_on_workspace_grant() -> None:
    store = _store()
    store.list_active_memberships.return_value = [
        _membership("tenant-a"),
        _membership("tenant-b"),
    ]

    result = await _service(store).login(
        EmailLoginCommand(
            email="owner@example.com",
            password="correct-password",
            correlation_id="correlation-a",
            source_fingerprint="browser-a",
        )
    )

    assert result.session is None
    assert result.completion_token
    grant = store.save_login_completion_grant.await_args.args[0]
    assert grant.authentication_method == "email_password"


@pytest.mark.asyncio
async def test_registration_rejects_invalid_or_consumed_invitation() -> None:
    store = _store()
    with pytest.raises(InvitationAccessError):
        await _service(store).register(
            RegisterWithInvitationCommand(
                invitation_token="x" * 48,
                display_name="New User",
                password="secure-password",
                correlation_id="correlation-a",
                source_fingerprint="browser-a",
            )
        )
    store.redeem_invitation.assert_not_awaited()


@pytest.mark.asyncio
async def test_invitation_link_uses_fragment_and_roles_from_trusted_command() -> None:
    store = _store()
    store.get_login_account.return_value = None

    async def create(invitation, *, correlation_id):  # type: ignore[no-untyped-def]
        del correlation_id
        return TenantInvitation(
            invitation_id=invitation.invitation_id,
            tenant_id=invitation.tenant_id,
            tenant_name="Tenant A",
            normalized_email=invitation.normalized_email,
            display_email=invitation.display_email,
            roles=invitation.roles,
            token_hash=invitation.token_hash,
            status=invitation.status,
            expires_at=invitation.expires_at,
            created_by=invitation.created_by,
            created_at=invitation.created_at,
        )

    store.create_invitation.side_effect = create
    principal = AuthenticatedPrincipal(
        subject_id="owner-a",
        tenant_id="tenant-a",
        roles=frozenset({"tenant_owner"}),
        scopes=frozenset({"users:manage"}),
        authentication_method="email_password",
        authenticated_at=datetime.now(UTC),
        correlation_id="correlation-a",
    )
    result = await InvitationManagementService(
        store=store,
        public_base_url="https://support.example.com",
    ).create_invitation(
        CreateTenantInvitationCommand(
            principal=principal,
            tenant_id="tenant-a",
            email="New.User@Example.com",
            roles=frozenset({"support_agent"}),
            expires_in_hours=72,
            correlation_id="correlation-a",
        )
    )

    assert result.invitation_url.startswith("https://support.example.com/#invite=")
    raw_token = result.invitation_url.split("=", 1)[1]
    assert raw_token not in result.invitation.token_hash
    assert result.invitation.normalized_email == "new.user@example.com"
    assert result.invitation.expires_at > datetime.now(UTC) + timedelta(hours=71)


def test_email_normalization_rejects_non_email_identifiers() -> None:
    assert normalize_email(" User@Example.COM ") == "user@example.com"
    with pytest.raises(ValueError):
        normalize_email("tenant-demo")
