from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event_id: str
    tenant_id: str
    event_type: str
    actor_subject_id: str | None
    correlation_id: str | None
    trace_id: str | None
    resource_type: str
    resource_id: str
    details: dict[str, object]
    created_at: datetime
