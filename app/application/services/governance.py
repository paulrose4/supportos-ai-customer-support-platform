from app.application.dto.governance import (
    AuditEventView,
    ListAuditEventsQuery,
    ListAuditEventsResult,
)
from app.domain.ports.governance import AuditLogPort

_SENSITIVE_KEY_PARTS = (
    "authorization",
    "cookie",
    "credential",
    "password",
    "secret",
    "site_key",
    "token",
    "api_key",
)


class AuditLogService:
    def __init__(self, audit_log: AuditLogPort) -> None:
        self._audit_log = audit_log

    async def list_events(self, query: ListAuditEventsQuery) -> ListAuditEventsResult:
        if "audit:read" not in query.principal.scopes:
            raise PermissionError("audit:read scope is required")
        limit = max(1, min(query.limit, 200))
        events = await self._audit_log.list_events(
            tenant_id=query.principal.tenant_id,
            event_type=_optional_filter(query.event_type),
            actor_subject_id=_optional_filter(query.actor_subject_id),
            resource_type=_optional_filter(query.resource_type),
            correlation_id=_optional_filter(query.correlation_id),
            created_from=query.created_from,
            created_to=query.created_to,
            cursor_created_at=query.cursor_created_at,
            cursor_event_id=query.cursor_event_id,
            limit=limit + 1,
        )
        has_more = len(events) > limit
        visible = events[:limit]
        views = tuple(
            AuditEventView(
                event_id=event.event_id,
                event_type=event.event_type,
                actor_subject_id=event.actor_subject_id,
                correlation_id=event.correlation_id,
                trace_id=event.trace_id,
                resource_type=event.resource_type,
                resource_id=event.resource_id,
                details=_redact_details(event.details),
                created_at=event.created_at,
            )
            for event in visible
        )
        cursor_event = visible[-1] if has_more and visible else None
        return ListAuditEventsResult(
            items=views,
            next_cursor_created_at=cursor_event.created_at if cursor_event else None,
            next_cursor_event_id=cursor_event.event_id if cursor_event else None,
        )


def _optional_filter(value: str | None) -> str | None:
    normalized = value.strip() if value is not None else ""
    return normalized or None


def _redact_details(value: object, key: str = "") -> object:
    normalized_key = key.casefold()
    if normalized_key and any(part in normalized_key for part in _SENSITIVE_KEY_PARTS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(item_key): _redact_details(item_value, str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_redact_details(item) for item in value]
    return value
