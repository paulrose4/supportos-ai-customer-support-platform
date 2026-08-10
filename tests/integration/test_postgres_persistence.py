import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, update

from app.domain.models import (
    AgentResponse,
    AuthenticatedPrincipal,
    HandoffRequest,
    ResponseKind,
    RiskLevel,
    ToolExecution,
)
from app.integrations.postgres import (
    DatabaseSessionManager,
    PostgreSQLConversationPersistenceAdapter,
    PostgreSQLQueueHandoffAdapter,
)
from app.integrations.postgres.models import (
    AgentRunModel,
    AuditEventModel,
    ConversationModel,
    HandoffRequestModel,
    MessageModel,
    SupportTicketModel,
    ToolExecutionModel,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION_TESTS") != "1",
        reason="set RUN_INTEGRATION_TESTS=1 with PostgreSQL running",
    ),
]

DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/customer_agent",
)


async def test_handoff_and_conversation_persistence_are_tenant_scoped_and_idempotent() -> None:
    tenant_id = f"test-{uuid4()}"
    conversation_id = str(uuid4())
    trace_id = str(uuid4())
    manager = DatabaseSessionManager(DATABASE_URL)
    handoff_adapter = PostgreSQLQueueHandoffAdapter(manager.session_factory)
    conversation_adapter = PostgreSQLConversationPersistenceAdapter(manager.session_factory)
    request = HandoffRequest(
        handoff_id=str(uuid4()),
        tenant_id=tenant_id,
        customer_id="customer-1",
        conversation_id=conversation_id,
        reason_code="high_impact_request",
        risk_level=int(RiskLevel.HIGH_IMPACT_REQUEST),
        summary="request refund",
        customer_language="en",
        identity_status="authenticated",
        user_intent="refund",
        unresolved_question="Confirm refund eligibility",
        ai_attempt="No approved automated answer was produced.",
        suggested_next_action="Review the order in the trusted system.",
        reply_draft="I am reviewing your refund request.",
        customer_sentiment="neutral",
        customer_request="request refund",
        idempotency_key="same-operation",
        trace_id=trace_id,
    )

    try:
        first = await handoff_adapter.create(request)
        second = await handoff_adapter.create(request)
        assert first.handoff_id == second.handoff_id
        queue = await handoff_adapter.list_for_tenant(
            tenant_id=tenant_id, status="pending", limit=10
        )
        other_tenant_queue = await handoff_adapter.list_for_tenant(
            tenant_id="other-tenant", status="pending", limit=10
        )
        assert [item.handoff_id for item in queue] == [first.handoff_id]
        assert other_tenant_queue == []

        async with manager.session_factory.begin() as session:
            await session.execute(
                update(HandoffRequestModel)
                .where(
                    HandoffRequestModel.tenant_id == tenant_id,
                    HandoffRequestModel.handoff_id == first.handoff_id,
                )
                .values(summary="contact legacy@example.com")
            )
        legacy_queue = await handoff_adapter.list_for_tenant(
            tenant_id=tenant_id, status="pending", limit=10
        )
        assert "legacy@example.com" not in legacy_queue[0].summary
        assert "[REDACTED_EMAIL]" in legacy_queue[0].summary

        principal = AuthenticatedPrincipal(
            subject_id="customer-1",
            tenant_id=tenant_id,
            roles=frozenset({"customer"}),
            scopes=frozenset(),
            authentication_method="mock",
            authenticated_at=datetime.now(UTC),
            correlation_id=str(uuid4()),
        )
        response = AgentResponse(
            conversation_id=conversation_id,
            message="已为您转交人工处理，请留意后续通知。",
            kind=ResponseKind.HANDOFF,
            risk_level=RiskLevel.HIGH_IMPACT_REQUEST,
            trace_id=trace_id,
            handoff_id=first.handoff_id,
            tool_executions=(
                ToolExecution(
                    execution_id=str(uuid4()),
                    tool_name="query_order_status",
                    status="succeeded",
                    input_summary={"resource_id_fingerprint": "abc123"},
                    output_summary={"found": True},
                    created_at=datetime.now(UTC),
                ),
            ),
        )
        received_at = datetime.now(UTC) - timedelta(milliseconds=50)
        await conversation_adapter.persist_exchange(
            principal=principal,
            user_message="我要退款",
            response=response,
            request_received_at=received_at,
            response_sent_at=datetime.now(UTC),
            request_route="/v1/public-widget/chat",
        )
        await conversation_adapter.persist_exchange(
            principal=principal,
            user_message="我要退款",
            response=response,
        )

        history = await conversation_adapter.load_recent(
            principal=principal,
            conversation_id=conversation_id,
            limit=8,
        )
        assert [turn.role for turn in history] == ["user", "assistant"]
        assert history[0].content == "我要退款"
        with pytest.raises(PermissionError, match="trusted principal"):
            await conversation_adapter.load_recent(
                principal=replace(principal, site_id="other-site"),
                conversation_id=conversation_id,
                limit=8,
            )

        async with manager.session() as session:
            assert await _count(session, HandoffRequestModel, tenant_id) == 1
            assert await _count(session, SupportTicketModel, tenant_id) == 0
            assert await _count(session, ConversationModel, tenant_id) == 1
            assert await _count(session, MessageModel, tenant_id) == 2
            assert await _count(session, AgentRunModel, tenant_id) == 1
            assert await _count(session, ToolExecutionModel, tenant_id) == 1
            assert await _count(session, AuditEventModel, tenant_id) == 2
            run = await session.scalar(
                select(AgentRunModel).where(AgentRunModel.tenant_id == tenant_id)
            )
            assert run is not None
            assert run.request_received_at == received_at
            assert run.response_sent_at is not None
            assert run.response_latency_ms is not None and run.response_latency_ms >= 50
            assert run.request_route == "/v1/public-widget/chat"
    finally:
        async with manager.session_factory.begin() as session:
            for model in (
                ToolExecutionModel,
                MessageModel,
                AgentRunModel,
                HandoffRequestModel,
                SupportTicketModel,
                ConversationModel,
                AuditEventModel,
            ):
                await session.execute(delete(model).where(model.tenant_id == tenant_id))
        await manager.dispose()


async def _count(session, model, tenant_id: str) -> int:  # type: ignore[no-untyped-def]
    statement = select(func.count()).select_from(model).where(model.tenant_id == tenant_id)
    return int(await session.scalar(statement) or 0)
