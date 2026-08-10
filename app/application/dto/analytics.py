from dataclasses import dataclass

from app.domain.models import AuthenticatedPrincipal
from app.domain.ports import SupportAnalyticsSnapshot


@dataclass(frozen=True, slots=True)
class GetSupportAnalyticsQuery:
    principal: AuthenticatedPrincipal
    days: int = 30
    site_id: str | None = None


@dataclass(frozen=True, slots=True)
class SupportAnalyticsResult:
    snapshot: SupportAnalyticsSnapshot
