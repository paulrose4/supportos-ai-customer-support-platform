from datetime import datetime
from typing import Protocol

from app.domain.models import (
    AdminSession,
    AdminUser,
    EmailIdentity,
    EmailLoginAccount,
    IdentityUser,
    LoginCompletionGrant,
    OAuthLoginState,
    PasswordResetToken,
    PlatformMembershipRecord,
    PlatformOnboardingSourceRecord,
    PlatformSiteRecord,
    PlatformSummary,
    PlatformTenantRecord,
    PlatformUserRecord,
    Tenant,
    TenantInvitation,
    TenantMembership,
    VerifiedExternalIdentity,
)


class AdminIdentityStorePort(Protocol):
    async def get_user_by_username(self, *, tenant_id: str, username: str) -> AdminUser | None: ...

    async def get_user_for_session(self, *, token_hash: str) -> AdminUser | None: ...

    async def get_user_by_id(self, *, tenant_id: str, user_id: str) -> AdminUser | None: ...

    async def list_users(self, *, tenant_id: str) -> list[AdminUser]: ...

    async def get_login_lock(
        self,
        *,
        source_fingerprint: str,
        checked_at: datetime,
    ) -> datetime | None: ...

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
    ) -> datetime | None: ...

    async def clear_login_failures(
        self,
        *,
        source_fingerprint: str,
        tenant_id: str,
        user_id: str,
        correlation_id: str,
        cleared_at: datetime,
    ) -> None: ...

    async def create_user_if_absent(self, user: AdminUser) -> AdminUser: ...

    async def create_managed_user(
        self,
        user: AdminUser,
        *,
        actor_subject_id: str,
        correlation_id: str,
    ) -> AdminUser: ...

    async def update_managed_user(
        self,
        *,
        tenant_id: str,
        user_id: str,
        display_name: str,
        roles: frozenset[str],
        scopes: frozenset[str],
        status: str,
        actor_subject_id: str,
        correlation_id: str,
        changed_at: datetime,
    ) -> AdminUser | None: ...

    async def reset_managed_password(
        self,
        *,
        tenant_id: str,
        user_id: str,
        expected_password_hash: str,
        new_password_hash: str,
        actor_subject_id: str,
        correlation_id: str,
        changed_at: datetime,
    ) -> bool: ...

    async def create_session(self, session: AdminSession) -> None: ...

    async def revoke_session(self, *, token_hash: str, revoked_at: datetime) -> bool: ...

    async def list_user_sessions(
        self, *, tenant_id: str, user_id: str, limit: int = 50
    ) -> list[AdminSession]: ...

    async def revoke_user_session(
        self,
        *,
        tenant_id: str,
        user_id: str,
        session_id: str,
        actor_subject_id: str,
        correlation_id: str,
        revoked_at: datetime,
    ) -> bool | None: ...

    async def change_password_and_revoke_sessions(
        self,
        *,
        tenant_id: str,
        user_id: str,
        expected_password_hash: str,
        new_password_hash: str,
        correlation_id: str,
        changed_at: datetime,
    ) -> bool: ...


class PasswordHasherPort(Protocol):
    def hash_password(self, password: str) -> str: ...

    def verify_password(self, password: str, encoded_hash: str) -> bool: ...


class TransactionalEmailPort(Protocol):
    async def send_email_verification(
        self,
        *,
        recipient: str,
        display_name: str,
        workspace_name: str,
        verification_url: str,
        expires_at: datetime,
    ) -> None: ...

    async def send_invitation(
        self,
        *,
        recipient: str,
        inviter_name: str,
        tenant_name: str,
        invitation_url: str,
        expires_at: datetime,
    ) -> None: ...

    async def send_password_reset(
        self,
        *,
        recipient: str,
        display_name: str,
        reset_url: str,
        expires_at: datetime,
    ) -> None: ...


class EmailIdentityStorePort(Protocol):
    async def get_login_account(self, *, normalized_email: str) -> EmailLoginAccount | None: ...

    async def get_login_account_for_session(
        self, *, token_hash: str
    ) -> EmailLoginAccount | None: ...

    async def get_email_identity_by_user_id(self, *, user_id: str) -> EmailIdentity | None: ...

    async def get_login_lock(
        self,
        *,
        source_fingerprint: str,
        email_hash: str,
        checked_at: datetime,
    ) -> datetime | None: ...

    async def record_login_failure(
        self,
        *,
        source_fingerprint: str,
        email_hash: str,
        correlation_id: str,
        occurred_at: datetime,
        max_attempts: int,
        window_seconds: int,
        lockout_seconds: int,
    ) -> datetime | None: ...

    async def clear_login_failures(
        self,
        *,
        source_fingerprint: str,
        email_hash: str,
        user_id: str,
        correlation_id: str,
        cleared_at: datetime,
    ) -> None: ...

    async def create_invitation(
        self,
        invitation: TenantInvitation,
        *,
        correlation_id: str,
    ) -> TenantInvitation: ...

    async def get_invitation_by_token(
        self, *, token_hash: str, checked_at: datetime
    ) -> TenantInvitation | None: ...

    async def list_invitations(
        self, *, tenant_id: str, limit: int = 100
    ) -> list[TenantInvitation]: ...

    async def revoke_invitation(
        self,
        *,
        tenant_id: str,
        invitation_id: str,
        actor_subject_id: str,
        correlation_id: str,
        revoked_at: datetime,
    ) -> TenantInvitation | None: ...

    async def redeem_invitation(
        self,
        *,
        token_hash: str,
        display_name: str,
        password_hash: str | None,
        existing_user_id: str | None,
        correlation_id: str,
        redeemed_at: datetime,
    ) -> EmailLoginAccount | None: ...

    async def save_login_completion_grant(self, grant: LoginCompletionGrant) -> None: ...

    async def list_active_memberships(self, *, user_id: str) -> list[TenantMembership]: ...

    async def create_session_for_workspace(self, session: AdminSession) -> AdminUser | None: ...

    async def create_password_reset(self, token: PasswordResetToken) -> None: ...

    async def reset_password(
        self,
        *,
        token_hash: str,
        new_password_hash: str,
        correlation_id: str,
        changed_at: datetime,
    ) -> bool: ...

    async def change_password_from_session(
        self,
        *,
        session_token_hash: str,
        expected_password_hash: str,
        new_password_hash: str,
        correlation_id: str,
        changed_at: datetime,
    ) -> bool: ...

    async def bootstrap_email_identity(
        self,
        *,
        user_id: str,
        normalized_email: str,
        display_email: str,
        password_hash: str,
        occurred_at: datetime,
    ) -> EmailLoginAccount: ...


class ExternalIdentityProviderPort(Protocol):
    @property
    def provider_name(self) -> str: ...

    def build_authorization_url(self, *, state: str, redirect_uri: str) -> str: ...

    async def exchange_authorization_code(
        self,
        *,
        code: str,
        redirect_uri: str,
    ) -> VerifiedExternalIdentity: ...


class WorkforceIdentityStorePort(Protocol):
    async def save_login_state(self, state: OAuthLoginState) -> None: ...

    async def consume_login_state(
        self,
        *,
        provider: str,
        state_hash: str,
        consumed_at: datetime,
    ) -> OAuthLoginState | None: ...

    async def upsert_external_user(
        self,
        *,
        identity: VerifiedExternalIdentity,
        correlation_id: str,
        occurred_at: datetime,
    ) -> IdentityUser: ...

    async def list_active_memberships(self, *, user_id: str) -> list[TenantMembership]: ...

    async def get_workspace_user(self, *, user_id: str, tenant_id: str) -> AdminUser | None: ...

    async def save_login_completion_grant(self, grant: LoginCompletionGrant) -> None: ...

    async def consume_login_completion_grant(
        self,
        *,
        token_hash: str,
        consumed_at: datetime,
    ) -> LoginCompletionGrant | None: ...

    async def get_login_completion_grant(
        self,
        *,
        token_hash: str,
        checked_at: datetime,
    ) -> LoginCompletionGrant | None: ...

    async def create_session(self, session: AdminSession) -> None: ...

    async def create_session_for_workspace(self, session: AdminSession) -> AdminUser | None: ...

    async def create_session_from_login_grant(
        self,
        *,
        grant_token_hash: str,
        tenant_id: str,
        session: AdminSession,
        consumed_at: datetime,
    ) -> AdminUser | None: ...

    async def rotate_session_workspace(
        self,
        *,
        current_token_hash: str,
        tenant_id: str,
        replacement: AdminSession,
        rotated_at: datetime,
    ) -> AdminUser | None: ...


class PlatformIdentityStorePort(Protocol):
    async def list_tenants(self) -> list[Tenant]: ...

    async def list_identity_users(self) -> list[IdentityUser]: ...

    async def provision_tenant(self, tenant: Tenant, *, actor_subject_id: str) -> Tenant: ...

    async def upsert_membership(
        self,
        membership: TenantMembership,
        *,
        actor_subject_id: str,
        correlation_id: str,
    ) -> TenantMembership: ...

    async def assign_platform_role(
        self,
        *,
        user_id: str,
        role: str,
        actor_subject_id: str,
        correlation_id: str,
        assigned_at: datetime,
    ) -> None: ...

    async def get_platform_summary(self, *, checked_at: datetime) -> PlatformSummary: ...

    async def list_platform_tenant_records(
        self,
        *,
        search: str,
        status: str,
        limit: int,
        after_updated_at: datetime | None,
        after_tenant_id: str | None,
    ) -> tuple[list[PlatformTenantRecord], int, bool]: ...

    async def get_platform_tenant_record(
        self, *, tenant_id: str
    ) -> PlatformTenantRecord | None: ...

    async def list_platform_memberships(
        self, *, tenant_id: str
    ) -> list[PlatformMembershipRecord]: ...

    async def list_platform_user_records(
        self,
        *,
        search: str,
        status: str,
        limit: int,
        after_updated_at: datetime | None,
        after_user_id: str | None,
    ) -> tuple[list[PlatformUserRecord], int, bool]: ...

    async def list_platform_onboarding_records(
        self,
        *,
        search: str,
    ) -> list[PlatformOnboardingSourceRecord]: ...

    async def revoke_platform_role(
        self,
        *,
        user_id: str,
        role: str,
        actor_subject_id: str,
        correlation_id: str,
        revoked_at: datetime,
    ) -> None: ...


class PlatformSiteDirectoryPort(Protocol):
    async def list_platform_site_records(
        self,
        *,
        search: str,
        tenant_id: str | None,
        status: str,
        verification_status: str,
        include_disabled: bool,
        limit: int,
        after_tenant_id: str | None,
        after_site_id: str | None,
        checked_at: datetime,
    ) -> tuple[list[PlatformSiteRecord], int, bool]: ...
