from dataclasses import dataclass

from app.domain.models import AuthenticatedPrincipal, ManagedSupportSite


@dataclass(frozen=True, slots=True)
class ListManagedSitesQuery:
    principal: AuthenticatedPrincipal


@dataclass(frozen=True, slots=True)
class CreateManagedSiteCommand:
    principal: AuthenticatedPrincipal
    site_id: str
    name: str
    base_url: str
    site_key: str | None
    correlation_id: str
    primary_language: str = "en"


@dataclass(frozen=True, slots=True)
class UpdateManagedSiteCommand:
    principal: AuthenticatedPrincipal
    site_id: str
    name: str
    base_url: str
    status: str
    correlation_id: str
    primary_language: str = "en"


@dataclass(frozen=True, slots=True)
class RotateManagedSiteKeyCommand:
    principal: AuthenticatedPrincipal
    site_id: str
    site_key: str
    correlation_id: str


@dataclass(frozen=True, slots=True)
class IssueSiteVerificationChallengeCommand:
    principal: AuthenticatedPrincipal
    site_id: str
    method: str
    correlation_id: str


@dataclass(frozen=True, slots=True)
class CompleteSiteVerificationCommand:
    principal: AuthenticatedPrincipal
    site_id: str
    method: str
    correlation_id: str


@dataclass(frozen=True, slots=True)
class SiteVerificationChallenge:
    site_id: str
    method: str
    dns_name: str
    dns_value: str
    script_path: str
    script_value: str
    expires_at: str
    verification_status: str


@dataclass(frozen=True, slots=True)
class ListManagedSitesResult:
    items: tuple[ManagedSupportSite, ...]
