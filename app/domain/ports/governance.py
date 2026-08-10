from datetime import datetime
from typing import Protocol

from app.domain.models.governance import AuditEvent


class AuditLogPort(Protocol):
    async def list_events(
        self,
        *,
        tenant_id: str,
        event_type: str | None,
        actor_subject_id: str | None,
        resource_type: str | None,
        correlation_id: str | None,
        created_from: datetime | None,
        created_to: datetime | None,
        cursor_created_at: datetime | None,
        cursor_event_id: str | None,
        limit: int,
    ) -> list[AuditEvent]: ...
