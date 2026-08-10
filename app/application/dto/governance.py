from dataclasses import dataclass
from datetime import datetime

from app.domain.models import AuthenticatedPrincipal


@dataclass(frozen=True, slots=True)
class ListAuditEventsQuery:
    principal: AuthenticatedPrincipal
    event_type: str | None = None
    actor_subject_id: str | None = None
    resource_type: str | None = None
    correlation_id: str | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None
    cursor_created_at: datetime | None = None
    cursor_event_id: str | None = None
    limit: int = 100


@dataclass(frozen=True, slots=True)
class AuditEventView:
    event_id: str
    event_type: str
    actor_subject_id: str | None
    correlation_id: str | None
    trace_id: str | None
    resource_type: str
    resource_id: str
    details: dict[str, object]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ListAuditEventsResult:
    items: tuple[AuditEventView, ...]
    next_cursor_created_at: datetime | None = None
    next_cursor_event_id: str | None = None
