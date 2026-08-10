from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class EnrollmentAuthority:
    authority_id: str
    name: str
    status: str
    max_active_tenants: int
    max_total_tenants: int
    active_tenant_count: int
    reserved_tenant_count: int
    total_tenant_count: int
    created_by: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class TenantEnrollmentPolicy:
    policy_id: str
    authority_id: str
    name: str
    enabled: bool
    allowed_email_domains: tuple[str, ...]
    require_exact_email_binding: bool
    default_plan_id: str
    require_email_verification: bool
    require_mfa: bool
    site_limit: int
    created_by: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class TenantEnrollmentCode:
    code_id: str
    policy_id: str
    code_digest: str
    code_key_version: str
    target_email: str
    code_prefix: str
    status: str
    expires_at: datetime
    created_by: str
    created_at: datetime
    reserved_by_intent_id: str | None = None
    reserved_until: datetime | None = None
    consumed_by_user_id: str | None = None
    consumed_tenant_id: str | None = None
    consumed_at: datetime | None = None
    revoked_by: str | None = None
    revoked_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class EnrollmentIntent:
    intent_id: str
    policy_id: str
    code_id: str
    normalized_email: str
    display_email: str
    display_name: str
    workspace_name: str
    password_hash: str | None
    existing_user_id: str | None
    proposed_user_id: str
    proposed_tenant_id: str
    request_hash: str
    idempotency_key_hash: str
    status_token_hash: str
    status: str
    expires_at: datetime
    created_at: datetime
    completed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class EmailVerificationToken:
    token_id: str
    intent_id: str
    token_hash: str
    expires_at: datetime
    created_at: datetime
    consumed_at: datetime | None = None
    revoked_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class EnrollmentEmailDelivery:
    delivery_id: str
    intent_id: str
    token_id: str
    recipient: str
    display_name: str
    workspace_name: str
    token_expires_at: datetime
    status: str
    attempts: int
    available_at: datetime
    created_at: datetime
    lease_until: datetime | None = None
    sent_at: datetime | None = None
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class EnrollmentCompletion:
    intent_id: str
    user_id: str
    tenant_id: str
    workspace_name: str
    completed_at: datetime
