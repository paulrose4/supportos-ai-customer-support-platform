from dataclasses import dataclass
from datetime import datetime

from app.domain.models import (
    AdminUser,
    AuthenticatedPrincipal,
    IssuedAdminSession,
    TenantInvitation,
    TenantMembership,
)


@dataclass(frozen=True, slots=True)
class LoginAdminCommand:
    tenant_id: str
    username: str
    password: str
    correlation_id: str
    source_fingerprint: str = "unknown"


@dataclass(frozen=True, slots=True)
class AuthenticateAdminCommand:
    session_token: str
    correlation_id: str


@dataclass(frozen=True, slots=True)
class LogoutAdminCommand:
    session_token: str
    correlation_id: str


@dataclass(frozen=True, slots=True)
class ChangeAdminPasswordCommand:
    session_token: str
    current_password: str
    new_password: str
    correlation_id: str


@dataclass(frozen=True, slots=True)
class BootstrapAdminCommand:
    tenant_id: str
    username: str
    password: str
    display_name: str


@dataclass(frozen=True, slots=True)
class LoginAdminResult:
    session: IssuedAdminSession


@dataclass(frozen=True, slots=True)
class AuthenticateAdminResult:
    principal: AuthenticatedPrincipal
    user: AdminUser


@dataclass(frozen=True, slots=True)
class ListAdminUsersQuery:
    principal: AuthenticatedPrincipal


@dataclass(frozen=True, slots=True)
class CreateAdminUserCommand:
    principal: AuthenticatedPrincipal
    username: str
    display_name: str
    password: str
    roles: frozenset[str]
    correlation_id: str


@dataclass(frozen=True, slots=True)
class UpdateAdminUserCommand:
    principal: AuthenticatedPrincipal
    user_id: str
    display_name: str
    roles: frozenset[str]
    status: str
    correlation_id: str


@dataclass(frozen=True, slots=True)
class ResetAdminUserPasswordCommand:
    principal: AuthenticatedPrincipal
    user_id: str
    new_password: str
    correlation_id: str


@dataclass(frozen=True, slots=True)
class ListAdminUsersResult:
    items: tuple[AdminUser, ...]


@dataclass(frozen=True, slots=True)
class ListAdminSessionsQuery:
    principal: AuthenticatedPrincipal
    current_session_token: str


@dataclass(frozen=True, slots=True)
class AdminSessionView:
    session_id: str
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    source_fingerprint_prefix: str
    is_current: bool


@dataclass(frozen=True, slots=True)
class ListAdminSessionsResult:
    items: tuple[AdminSessionView, ...]


@dataclass(frozen=True, slots=True)
class RevokeAdminSessionCommand:
    principal: AuthenticatedPrincipal
    current_session_token: str
    session_id: str
    correlation_id: str


@dataclass(frozen=True, slots=True)
class RevokeAdminSessionResult:
    session_id: str
    revoked: bool
    changed: bool
    was_current: bool


@dataclass(frozen=True, slots=True)
class StartExternalLoginCommand:
    provider: str
    redirect_uri: str
    return_path: str


@dataclass(frozen=True, slots=True)
class StartExternalLoginResult:
    authorization_url: str


@dataclass(frozen=True, slots=True)
class CompleteExternalLoginCommand:
    provider: str
    code: str
    state: str
    redirect_uri: str
    source_fingerprint: str
    correlation_id: str


@dataclass(frozen=True, slots=True)
class CompleteExternalLoginResult:
    session: IssuedAdminSession | None
    completion_token: str | None
    return_path: str
    memberships: tuple[TenantMembership, ...]


@dataclass(frozen=True, slots=True)
class ListWorkspacesQuery:
    principal: AuthenticatedPrincipal | None
    completion_token: str | None


@dataclass(frozen=True, slots=True)
class ListWorkspacesResult:
    items: tuple[TenantMembership, ...]
    active_tenant_id: str | None


@dataclass(frozen=True, slots=True)
class SelectWorkspaceCommand:
    tenant_id: str
    source_fingerprint: str
    correlation_id: str
    principal: AuthenticatedPrincipal | None = None
    completion_token: str | None = None
    current_session_token: str | None = None


@dataclass(frozen=True, slots=True)
class SelectWorkspaceResult:
    session: IssuedAdminSession


@dataclass(frozen=True, slots=True)
class EmailLoginCommand:
    email: str
    password: str
    correlation_id: str
    source_fingerprint: str


@dataclass(frozen=True, slots=True)
class RegisterWithInvitationCommand:
    invitation_token: str
    display_name: str
    password: str
    correlation_id: str
    source_fingerprint: str


@dataclass(frozen=True, slots=True)
class EmailAuthenticationResult:
    session: IssuedAdminSession | None
    completion_token: str | None


@dataclass(frozen=True, slots=True)
class InvitationPreviewResult:
    tenant_name: str
    email: str
    roles: tuple[str, ...]
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class CreateTenantInvitationCommand:
    principal: AuthenticatedPrincipal
    tenant_id: str
    email: str
    roles: frozenset[str]
    expires_in_hours: int
    correlation_id: str


@dataclass(frozen=True, slots=True)
class CreateTenantInvitationResult:
    invitation: TenantInvitation
    invitation_url: str
    email_sent: bool


@dataclass(frozen=True, slots=True)
class ListTenantInvitationsQuery:
    principal: AuthenticatedPrincipal
    tenant_id: str


@dataclass(frozen=True, slots=True)
class ListTenantInvitationsResult:
    items: tuple[TenantInvitation, ...]


@dataclass(frozen=True, slots=True)
class RevokeTenantInvitationCommand:
    principal: AuthenticatedPrincipal
    tenant_id: str
    invitation_id: str
    correlation_id: str


@dataclass(frozen=True, slots=True)
class RequestPasswordResetCommand:
    email: str
    correlation_id: str


@dataclass(frozen=True, slots=True)
class ResetEmailPasswordCommand:
    reset_token: str
    new_password: str
    correlation_id: str


@dataclass(frozen=True, slots=True)
class ChangeEmailPasswordCommand:
    session_token: str
    current_password: str
    new_password: str
    correlation_id: str
