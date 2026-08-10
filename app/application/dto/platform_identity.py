from dataclasses import dataclass
from datetime import datetime

from app.domain.models import (
    AuthenticatedPrincipal,
    IdentityUser,
    PlatformMembershipRecord,
    PlatformSiteRecord,
    PlatformSummary,
    PlatformTenantRecord,
    PlatformUserRecord,
    Tenant,
)


@dataclass(frozen=True, slots=True)
class ListPlatformTenantsQuery:
    principal: AuthenticatedPrincipal


@dataclass(frozen=True, slots=True)
class ListPlatformTenantsResult:
    items: tuple[Tenant, ...]


@dataclass(frozen=True, slots=True)
class ListPlatformUsersQuery:
    principal: AuthenticatedPrincipal


@dataclass(frozen=True, slots=True)
class ListPlatformUsersResult:
    items: tuple[IdentityUser, ...]


@dataclass(frozen=True, slots=True)
class CreateTenantCommand:
    principal: AuthenticatedPrincipal
    tenant_id: str
    name: str


@dataclass(frozen=True, slots=True)
class UpsertTenantMembershipCommand:
    principal: AuthenticatedPrincipal
    tenant_id: str
    user_id: str
    roles: frozenset[str]
    status: str
    correlation_id: str


@dataclass(frozen=True, slots=True)
class AssignPlatformRoleCommand:
    principal: AuthenticatedPrincipal
    user_id: str
    role: str
    correlation_id: str


@dataclass(frozen=True, slots=True)
class GetPlatformSummaryQuery:
    principal: AuthenticatedPrincipal


@dataclass(frozen=True, slots=True)
class PlatformSummaryResult:
    summary: PlatformSummary


@dataclass(frozen=True, slots=True)
class ListPlatformTenantRecordsQuery:
    principal: AuthenticatedPrincipal
    search: str = ""
    status: str = ""
    limit: int = 25
    cursor: str | None = None


@dataclass(frozen=True, slots=True)
class PlatformTenantRecordsResult:
    items: tuple[PlatformTenantRecord, ...]
    total: int
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class ListPlatformSiteRecordsQuery:
    principal: AuthenticatedPrincipal
    search: str = ""
    tenant_id: str | None = None
    status: str = ""
    verification_status: str = ""
    include_disabled: bool = True
    limit: int = 25
    cursor: str | None = None


@dataclass(frozen=True, slots=True)
class PlatformSiteRecordsResult:
    items: tuple[PlatformSiteRecord, ...]
    total: int
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class GetPlatformTenantQuery:
    principal: AuthenticatedPrincipal
    tenant_id: str


@dataclass(frozen=True, slots=True)
class ListPlatformMembershipsQuery:
    principal: AuthenticatedPrincipal
    tenant_id: str


@dataclass(frozen=True, slots=True)
class PlatformMembershipsResult:
    tenant_name: str
    items: tuple[PlatformMembershipRecord, ...]


@dataclass(frozen=True, slots=True)
class ListPlatformUserRecordsQuery:
    principal: AuthenticatedPrincipal
    search: str = ""
    status: str = ""
    limit: int = 25
    cursor: str | None = None


@dataclass(frozen=True, slots=True)
class PlatformUserRecordsResult:
    items: tuple[PlatformUserRecord, ...]
    total: int
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class PlatformOnboardingRecord:
    code_id: str
    target_email: str
    status: str
    expires_at: datetime
    created_by: str
    created_by_name: str | None
    created_at: datetime
    workspace_name: str | None
    tenant_id: str | None
    email_status: str | None
    email_attempts: int
    email_sent_at: datetime | None
    email_last_error: str | None


@dataclass(frozen=True, slots=True)
class ListPlatformOnboardingRecordsQuery:
    principal: AuthenticatedPrincipal
    search: str = ""
    status: str = ""
    limit: int = 25
    cursor: str | None = None


@dataclass(frozen=True, slots=True)
class PlatformOnboardingRecordsResult:
    items: tuple[PlatformOnboardingRecord, ...]
    total: int
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class RevokePlatformRoleCommand:
    principal: AuthenticatedPrincipal
    user_id: str
    role: str
    correlation_id: str
