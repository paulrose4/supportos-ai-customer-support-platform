from dataclasses import dataclass
from datetime import datetime

from app.domain.models import AuthenticatedPrincipal


@dataclass(frozen=True, slots=True)
class GetSiteKnowledgeReadinessQuery:
    principal: AuthenticatedPrincipal
    site_id: str
    language: str = "en"


@dataclass(frozen=True, slots=True)
class SiteKnowledgeReadinessResult:
    site_id: str
    ready: bool
    catalog_ready: bool
    policy_ready: bool
    care_ready: bool
    active_product_count: int
    active_document_count: int
    active_snapshot_id: str | None
    last_successful_sync_at: datetime | None
    blocking_reasons: tuple[str, ...]
