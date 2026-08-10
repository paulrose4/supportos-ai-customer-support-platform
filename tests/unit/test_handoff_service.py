from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.application.dto import CreateHandoffCommand, ListHandoffsQuery
from app.application.services import CreateHandoffService, ListHandoffsService
from app.domain.models import AuthenticatedPrincipal, RiskLevel
from tests.fakes.adapters import InMemoryHandoffAdapter


def principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        subject_id="customer-1",
        tenant_id="tenant-a",
        roles=frozenset({"customer"}),
        scopes=frozenset(),
        authentication_method="mock",
        authenticated_at=datetime.now(UTC),
        correlation_id="correlation-1",
    )


async def test_handoff_service_is_idempotent_for_same_run() -> None:
    adapter = InMemoryHandoffAdapter()
    service = CreateHandoffService(adapter)
    command = CreateHandoffCommand(
        principal=principal(),
        conversation_id="conversation-1",
        reason_code="high_impact_request",
        risk_level=RiskLevel.HIGH_IMPACT_REQUEST,
        summary="我要退款",
        trace_id="trace-1",
    )

    first = await service.execute(command)
    second = await service.execute(command)

    assert first.handoff_id == second.handoff_id
    assert first.idempotency_key == second.idempotency_key
    assert len(adapter.requests) == 1
    assert first.context_schema_version == 2
    assert first.customer_language == "und"
    assert first.identity_status == "authenticated"
    assert first.customer_request == "我要退款"
    assert first.user_intent == "unclassified"
    assert first.unresolved_question == "我要退款"
    assert first.ai_attempt
    assert first.suggested_next_action
    assert first.reply_draft


async def test_handoff_summary_is_redacted_before_write() -> None:
    adapter = InMemoryHandoffAdapter()
    result = await CreateHandoffService(adapter).execute(
        CreateHandoffCommand(
            principal=principal(),
            conversation_id="conversation-1",
            reason_code="knowledge_insufficient",
            risk_level=RiskLevel.PUBLIC,
            summary="邮箱 me@example.com 无法查询",
            trace_id="trace-1",
        )
    )

    assert "me@example.com" not in result.summary
    assert "[REDACTED_EMAIL]" in result.summary


async def test_handoff_queue_requires_scope_and_uses_trusted_tenant() -> None:
    adapter = InMemoryHandoffAdapter()
    creator = CreateHandoffService(adapter)
    await creator.execute(
        CreateHandoffCommand(
            principal=principal(),
            conversation_id="conversation-1",
            reason_code="high_impact_request",
            risk_level=RiskLevel.HIGH_IMPACT_REQUEST,
            summary="退款请求",
            trace_id="trace-1",
        )
    )
    service = ListHandoffsService(adapter)

    with pytest.raises(PermissionError):
        await service.execute(ListHandoffsQuery(principal=principal()))

    authorized = replace(principal(), scopes=frozenset({"handoffs:read"}))
    result = await service.execute(ListHandoffsQuery(principal=authorized))

    assert len(result.handoffs) == 1
    assert result.handoffs[0].tenant_id == "tenant-a"


class RecordingNotificationPort:
    def __init__(self) -> None:
        self.items = []

    async def send(self, notification):  # type: ignore[no-untyped-def]
        self.items.append(notification)


async def test_handoff_notifies_site_agent_once() -> None:
    adapter = InMemoryHandoffAdapter()
    notifications = RecordingNotificationPort()
    service = CreateHandoffService(adapter, notifications)
    command = CreateHandoffCommand(
        principal=replace(principal(), site_id="site-a"),
        conversation_id="conversation-1",
        reason_code="knowledge_insufficient",
        risk_level=RiskLevel.PUBLIC,
        summary="需要人工",
        trace_id="trace-1",
    )

    await service.execute(command)
    await service.execute(command)

    assert len(notifications.items) == 1
    assert notifications.items[0].site_id == "site-a"
