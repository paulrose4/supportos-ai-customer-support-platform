from dataclasses import dataclass
from datetime import datetime

from app.domain.models import AuthenticatedPrincipal
from app.domain.models.onboarding import (
    EnrollmentAuthority,
    TenantEnrollmentCode,
    TenantEnrollmentPolicy,
)


@dataclass(frozen=True, slots=True)
class CreateEnrollmentAuthorityCommand:
    principal: AuthenticatedPrincipal
    authority_id: str
    name: str
    max_active_tenants: int
    max_total_tenants: int
    correlation_id: str


@dataclass(frozen=True, slots=True)
class CreateEnrollmentPolicyCommand:
    principal: AuthenticatedPrincipal
    policy_id: str
    authority_id: str
    name: str
    allowed_email_domains: tuple[str, ...]
    require_exact_email_binding: bool
    default_plan_id: str
    require_mfa: bool
    site_limit: int
    correlation_id: str


@dataclass(frozen=True, slots=True)
class IssueEnrollmentCodeCommand:
    principal: AuthenticatedPrincipal
    policy_id: str
    target_email: str
    expires_in_hours: int
    correlation_id: str


@dataclass(frozen=True, slots=True)
class IssueEnrollmentCodeResult:
    code: TenantEnrollmentCode
    enrollment_code: str


@dataclass(frozen=True, slots=True)
class IssueWorkspaceOnboardingCodeCommand:
    principal: AuthenticatedPrincipal
    target_email: str
    expires_in_hours: int
    site_limit: int
    correlation_id: str


@dataclass(frozen=True, slots=True)
class IssueWorkspaceOnboardingCodeResult:
    code: TenantEnrollmentCode
    enrollment_code: str
    signup_url: str


@dataclass(frozen=True, slots=True)
class ListEnrollmentCodesQuery:
    principal: AuthenticatedPrincipal
    policy_id: str


@dataclass(frozen=True, slots=True)
class ListEnrollmentCodesResult:
    items: tuple[TenantEnrollmentCode, ...]


@dataclass(frozen=True, slots=True)
class RevokeEnrollmentCodeCommand:
    principal: AuthenticatedPrincipal
    code_id: str
    correlation_id: str


@dataclass(frozen=True, slots=True)
class StartSelfServiceSignupCommand:
    email: str
    password: str
    display_name: str
    workspace_name: str
    enrollment_code: str
    idempotency_key: str
    source_fingerprint: str
    correlation_id: str


@dataclass(frozen=True, slots=True)
class StartSelfServiceSignupResult:
    status: str
    status_token: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class VerifySelfServiceEmailCommand:
    verification_token: str
    source_fingerprint: str
    correlation_id: str


@dataclass(frozen=True, slots=True)
class VerifySelfServiceEmailResult:
    status: str
    tenant_id: str
    workspace_name: str


@dataclass(frozen=True, slots=True)
class ResendSelfServiceVerificationCommand:
    status_token: str
    source_fingerprint: str
    correlation_id: str


@dataclass(frozen=True, slots=True)
class GetSelfServiceSignupStatusQuery:
    status_token: str


@dataclass(frozen=True, slots=True)
class SelfServiceSignupStatusResult:
    status: str
    expires_at: datetime
    tenant_id: str | None
    workspace_name: str


__all__ = [
    "CreateEnrollmentAuthorityCommand",
    "CreateEnrollmentPolicyCommand",
    "GetSelfServiceSignupStatusQuery",
    "IssueEnrollmentCodeCommand",
    "IssueEnrollmentCodeResult",
    "IssueWorkspaceOnboardingCodeCommand",
    "IssueWorkspaceOnboardingCodeResult",
    "ListEnrollmentCodesQuery",
    "ListEnrollmentCodesResult",
    "ResendSelfServiceVerificationCommand",
    "RevokeEnrollmentCodeCommand",
    "SelfServiceSignupStatusResult",
    "StartSelfServiceSignupCommand",
    "StartSelfServiceSignupResult",
    "VerifySelfServiceEmailCommand",
    "VerifySelfServiceEmailResult",
    "EnrollmentAuthority",
    "TenantEnrollmentPolicy",
]
