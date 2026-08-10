from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class RealtimeEvent:
    event_id: str
    tenant_id: str
    event_type: str
    resource_type: str
    resource_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    occurred_at: datetime | None = None
