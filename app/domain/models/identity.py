from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class AdminUser:
    user_id: str
    tenant_id: str
    username: str
    display_name: str
    password_hash: str
    roles: frozenset[str]
    scopes: frozenset[str]
    status: str
    created_at: datetime
    updated_at: datetime
    authentication_method: str = "local"
    platform_roles: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class AdminSession:
    session_id: str
    tenant_id: str
    user_id: str
    token_hash: str
    expires_at: datetime
    created_at: datetime
    last_seen_at: datetime
    source_fingerprint: str = "unknown"
    revoked_at: datetime | None = None
    authentication_method: str = "local"


@dataclass(frozen=True, slots=True)
class IssuedAdminSession:
    token: str
    expires_at: datetime
    user: AdminUser


@dataclass(frozen=True, slots=True)
class Tenant:
    tenant_id: str
    name: str
    status: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class IdentityUser:
    user_id: str
    display_name: str
    status: str
    created_at: datetime
    updated_at: datetime
    primary_email: str | None = None


@dataclass(frozen=True, slots=True)
class EmailIdentity:
    identity_id: str
    user_id: str
    normalized_email: str
    display_email: str
    status: str
    created_at: datetime
    updated_at: datetime
    verified_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class EmailLoginAccount:
    user: IdentityUser
    identity: EmailIdentity
    password_hash: str
    password_version: int


@dataclass(frozen=True, slots=True)
class TenantInvitation:
    invitation_id: str
    tenant_id: str
    tenant_name: str
    normalized_email: str
    display_email: str
    roles: frozenset[str]
    token_hash: str
    status: str
    expires_at: datetime
    created_by: str
    created_at: datetime
    redeemed_by: str | None = None
    redeemed_at: datetime | None = None
    revoked_by: str | None = None
    revoked_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PasswordResetToken:
    reset_id: str
    user_id: str
    token_hash: str
    expires_at: datetime
    created_at: datetime
    consumed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ExternalIdentity:
    identity_id: str
    user_id: str
    provider: str
    organization_id: str
    provider_subject_id: str
    provider_user_id: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class VerifiedExternalIdentity:
    provider: str
    organization_id: str
    provider_subject_id: str
    provider_user_id: str | None
    display_name: str


@dataclass(frozen=True, slots=True)
class TenantMembership:
    membership_id: str
    tenant_id: str
    tenant_name: str
    user_id: str
    roles: frozenset[str]
    scopes: frozenset[str]
    status: str
    created_at: datetime
    updated_at: datetime
    source: str = "admin"
    approval_status: str = "approved"
    activated_at: datetime | None = None
    deactivated_at: datetime | None = None
    created_by: str | None = None


@dataclass(frozen=True, slots=True)
class OAuthLoginState:
    state_id: str
    provider: str
    state_hash: str
    return_path: str
    expires_at: datetime
    created_at: datetime
    consumed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class LoginCompletionGrant:
    grant_id: str
    user_id: str
    token_hash: str
    expires_at: datetime
    created_at: datetime
    consumed_at: datetime | None = None
    authentication_method: str = "dingtalk"
