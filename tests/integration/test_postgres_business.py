import os
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from app.integrations.postgres import (
    DatabaseSessionManager,
    PostgreSQLBusinessReadAdapter,
    PostgreSQLMockBusinessDataSeeder,
    PostgreSQLOrderReadAdapter,
    PostgreSQLSupportTicketReadAdapter,
)
from app.integrations.postgres.models import (
    AuditEventModel,
    CustomerModel,
    OrderModel,
    SupportTicketModel,
    TenantModel,
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


async def test_mock_business_seed_and_owned_reads_are_idempotent_and_tenant_scoped() -> None:
    tenant_id = f"test-{uuid4()}"
    customer_id = "synthetic-customer"
    manager = DatabaseSessionManager(DATABASE_URL)
    seeder = PostgreSQLMockBusinessDataSeeder(manager.session_factory)
    delegate = PostgreSQLBusinessReadAdapter(manager.session_factory)
    orders = PostgreSQLOrderReadAdapter(delegate)
    tickets = PostgreSQLSupportTicketReadAdapter(delegate)

    try:
        await seeder.seed(tenant_id=tenant_id, customer_id=customer_id)
        await seeder.seed(tenant_id=tenant_id, customer_id=customer_id)

        order = await orders.get_for_customer(
            tenant_id=tenant_id,
            customer_id=customer_id,
            order_id="DEMO-ORDER-1001",
        )
        ticket = await tickets.get_for_customer(
            tenant_id=tenant_id,
            customer_id=customer_id,
            ticket_id="DEMO-TICKET-2001",
        )
        wrong_owner = await orders.get_for_customer(
            tenant_id=tenant_id,
            customer_id="other-customer",
            order_id="DEMO-ORDER-1001",
        )
        wrong_tenant = await orders.get_for_customer(
            tenant_id="other-tenant",
            customer_id=customer_id,
            order_id="DEMO-ORDER-1001",
        )

        assert order is not None and order.status == "shipped"
        assert ticket is not None and ticket.status == "open"
        assert wrong_owner is None
        assert wrong_tenant is None

        async with manager.session() as session:
            tenant = await session.scalar(
                select(TenantModel).where(TenantModel.tenant_id == tenant_id)
            )
            assert tenant is not None
            assert tenant.name == "演示工作区"
            assert await _count(session, CustomerModel, tenant_id) == 1
            assert await _count(session, OrderModel, tenant_id) == 1
            assert await _count(session, SupportTicketModel, tenant_id) == 1
            seeded_events = await session.scalar(
                select(func.count())
                .select_from(AuditEventModel)
                .where(
                    AuditEventModel.tenant_id == tenant_id,
                    AuditEventModel.event_type == "mock_business_data.seeded",
                )
            )
            assert int(seeded_events or 0) == 1
    finally:
        async with manager.session_factory.begin() as session:
            for model in (SupportTicketModel, OrderModel, CustomerModel, AuditEventModel):
                await session.execute(delete(model).where(model.tenant_id == tenant_id))
            await session.execute(delete(TenantModel).where(TenantModel.tenant_id == tenant_id))
        await manager.dispose()


async def _count(session, model, tenant_id: str) -> int:  # type: ignore[no-untyped-def]
    statement = select(func.count()).select_from(model).where(model.tenant_id == tenant_id)
    return int(await session.scalar(statement) or 0)
