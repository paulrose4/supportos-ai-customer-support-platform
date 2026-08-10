from datetime import UTC, datetime

import pytest

from app.application.dto import QueryBusinessDataCommand
from app.application.services import QueryBusinessDataService
from app.domain.models import AuthenticatedPrincipal
from tests.fakes.adapters import InMemoryOrderRepository, InMemorySupportTicketRepository


def principal(*, scopes: frozenset[str] | None = None) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        subject_id="trusted-customer",
        tenant_id="tenant-a",
        roles=frozenset({"customer"}),
        scopes=scopes or frozenset({"orders:read:self", "tickets:read:self"}),
        authentication_method="mock",
        authenticated_at=datetime.now(UTC),
        correlation_id="correlation-1",
    )


@pytest.mark.parametrize(
    "message",
    (
        "查询订单状态 DEMO-ORDER-1001",
        "查询我的订单状态",
        "Where is my order ORDER-1001?",
    ),
)
async def test_business_service_never_queries_order_repository(message: str) -> None:
    orders = InMemoryOrderRepository()
    service = QueryBusinessDataService(
        orders=orders,
        support_tickets=InMemorySupportTicketRepository(),
    )

    result = await service.execute(
        QueryBusinessDataCommand(
            principal=principal(),
            message=message,
            trace_id="trace-1",
        )
    )

    assert result.status == "human_required"
    assert "人工客服" in (result.message or "") or "human support" in (result.message or "")
    assert result.tool_executions == ()
    assert result.evidence == ()
    assert orders.calls == []


async def test_business_service_never_queries_support_ticket_repository() -> None:
    tickets = InMemorySupportTicketRepository()
    service = QueryBusinessDataService(
        orders=InMemoryOrderRepository(),
        support_tickets=tickets,
    )

    result = await service.execute(
        QueryBusinessDataCommand(
            principal=principal(),
            message="查询工单状态 DEMO-TICKET-2001",
            trace_id="trace-1",
        )
    )

    assert result.status == "human_required"
    assert result.tool_executions == ()
    assert result.evidence == ()
    assert tickets.calls == []
