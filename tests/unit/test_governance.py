from datetime import UTC, datetime, timedelta

import pytest

from app.application.dto import ListAuditEventsQuery
from app.application.services import AuditLogService
from app.domain.models import AuditEvent, AuthenticatedPrincipal


class FakeAuditLog:
    def __init__(self, events: list[AuditEvent]) -> None:
        self.events = events
        self.last_tenant_id: str | None = None

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
    ) -> list[AuditEvent]:
        self.last_tenant_id = tenant_id
        items = [event for event in self.events if event.tenant_id == tenant_id]
        if event_type:
            items = [event for event in items if event.event_type == event_type]
        if actor_subject_id:
            items = [event for event in items if event.actor_subject_id == actor_subject_id]
        if resource_type:
            items = [event for event in items if event.resource_type == resource_type]
        if correlation_id:
            items = [event for event in items if event.correlation_id == correlation_id]
        if created_from:
            items = [event for event in items if event.created_at >= created_from]
        if created_to:
            items = [event for event in items if event.created_at <= created_to]
        if cursor_created_at and cursor_event_id:
            items = [
                event
                for event in items
                if (event.created_at, event.event_id) < (cursor_created_at, cursor_event_id)
            ]
        return sorted(
            items,
            key=lambda event: (event.created_at, event.event_id),
            reverse=True,
        )[:limit]


def _principal(*scopes: str) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        subject_id="owner-1",
        tenant_id="tenant-a",
        roles=frozenset({"tenant_owner"}),
        scopes=frozenset(scopes),
        authentication_method="admin_session",
        authenticated_at=datetime.now(UTC),
        correlation_id="request-1",
    )


@pytest.mark.asyncio
async def test_audit_log_is_tenant_scoped_paginated_and_redacted() -> None:
    now = datetime.now(UTC)
    events = [
        AuditEvent(
            event_id=f"event-{index}",
            tenant_id="tenant-a",
            event_type="admin_user.updated",
            actor_subject_id="owner-1",
            correlation_id=f"correlation-{index}",
            trace_id=None,
            resource_type="admin_user",
            resource_id=f"user-{index}",
            details={
                "display_name": "Agent",
                "nested": {"site_key": "must-not-leak"},
                "access_token": "must-not-leak",
            },
            created_at=now - timedelta(minutes=index),
        )
        for index in range(3)
    ]
    events.append(
        AuditEvent(
            event_id="other-tenant",
            tenant_id="tenant-b",
            event_type="admin_user.updated",
            actor_subject_id="owner-b",
            correlation_id=None,
            trace_id=None,
            resource_type="admin_user",
            resource_id="user-b",
            details={},
            created_at=now,
        )
    )
    adapter = FakeAuditLog(events)
    service = AuditLogService(adapter)

    first = await service.list_events(
        ListAuditEventsQuery(principal=_principal("audit:read"), limit=2)
    )
    second = await service.list_events(
        ListAuditEventsQuery(
            principal=_principal("audit:read"),
            cursor_created_at=first.next_cursor_created_at,
            cursor_event_id=first.next_cursor_event_id,
            limit=2,
        )
    )

    assert adapter.last_tenant_id == "tenant-a"
    assert [item.event_id for item in first.items] == ["event-0", "event-1"]
    assert [item.event_id for item in second.items] == ["event-2"]
    assert first.items[0].details["access_token"] == "[REDACTED]"
    assert first.items[0].details["nested"] == {"site_key": "[REDACTED]"}


@pytest.mark.asyncio
async def test_audit_log_requires_audit_read_scope() -> None:
    service = AuditLogService(FakeAuditLog([]))

    with pytest.raises(PermissionError, match="audit:read"):
        await service.list_events(ListAuditEventsQuery(principal=_principal()))
