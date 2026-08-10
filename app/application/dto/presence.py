from dataclasses import dataclass

from app.application.dto.lead_scoring import LeadScoreResult
from app.domain.models import AuthenticatedPrincipal, VisitorPresence


@dataclass(frozen=True, slots=True)
class RecordVisitorPresenceCommand:
    principal: AuthenticatedPrincipal
    visitor_id: str
    conversation_id: str | None
    page_path: str
    page_title: str | None = None
    referrer: str | None = None
    source_address: str | None = None
    country_code: str | None = None
    user_agent: str | None = None
    language: str | None = None
    timezone: str | None = None
    page_view_id: str | None = None
    widget_state: str | None = None
    presence_source: str | None = None
    page_kind: str | None = None
    runtime_version: str | None = None
    config_version: str | None = None
    connector_type: str | None = None
    connector_version: str | None = None


@dataclass(frozen=True, slots=True)
class ListVisitorPresenceQuery:
    principal: AuthenticatedPrincipal
    active_within_seconds: int = 60


@dataclass(frozen=True, slots=True)
class ListVisitorPresenceResult:
    items: tuple[VisitorPresence, ...]
    scores: tuple[LeadScoreResult, ...]

    def __post_init__(self) -> None:
        if len(self.items) != len(self.scores):
            raise ValueError("presence items and scores must have the same length")
