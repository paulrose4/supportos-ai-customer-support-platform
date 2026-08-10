import base64
import json
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.dependencies import authenticate_admin_request, get_container
from app.api.schemas.governance import AuditEventListResponse, AuditEventResponse
from app.application.dto import ListAuditEventsQuery
from app.bootstrap.container import Container

router = APIRouter(prefix="/v1/admin/audit-events", tags=["governance"])


@router.get("", response_model=AuditEventListResponse)
async def list_audit_events(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
    event_type: str | None = Query(default=None, max_length=100),
    actor_subject_id: str | None = Query(default=None, max_length=100),
    resource_type: str | None = Query(default=None, max_length=100),
    correlation_id: str | None = Query(default=None, max_length=100),
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    cursor: str | None = Query(default=None, max_length=500),
    limit: int = Query(default=100, ge=1, le=200),
) -> AuditEventListResponse:
    principal = await authenticate_admin_request(request, container)
    cursor_created_at, cursor_event_id = _decode_cursor(cursor)
    try:
        result = await container.audit_log_service.list_events(
            ListAuditEventsQuery(
                principal=principal,
                event_type=event_type,
                actor_subject_id=actor_subject_id,
                resource_type=resource_type,
                correlation_id=correlation_id,
                created_from=created_from,
                created_to=created_to,
                cursor_created_at=cursor_created_at,
                cursor_event_id=cursor_event_id,
                limit=limit,
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    next_cursor = None
    if result.next_cursor_created_at is not None and result.next_cursor_event_id is not None:
        next_cursor = _encode_cursor(result.next_cursor_created_at, result.next_cursor_event_id)
    return AuditEventListResponse(
        items=[
            AuditEventResponse(
                event_id=item.event_id,
                event_type=item.event_type,
                actor_subject_id=item.actor_subject_id,
                correlation_id=item.correlation_id,
                trace_id=item.trace_id,
                resource_type=item.resource_type,
                resource_id=item.resource_id,
                details=item.details,
                created_at=item.created_at.isoformat(),
            )
            for item in result.items
        ],
        next_cursor=next_cursor,
    )


def _encode_cursor(created_at: datetime, event_id: str) -> str:
    payload = json.dumps(
        {"created_at": created_at.isoformat(), "event_id": event_id},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None) -> tuple[datetime | None, str | None]:
    if not cursor:
        return None, None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        created_at = datetime.fromisoformat(payload["created_at"])
        event_id = str(payload["event_id"])
        if not event_id:
            raise ValueError
        return created_at, event_id
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail="invalid audit cursor") from exc
