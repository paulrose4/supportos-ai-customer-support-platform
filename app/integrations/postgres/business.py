from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.models import Order, SupportTicket
from app.integrations.postgres.models import OrderModel, SupportTicketModel


class PostgreSQLBusinessReadAdapter:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_for_customer(
        self,
        *,
        tenant_id: str,
        customer_id: str,
        order_id: str | None = None,
        ticket_id: str | None = None,
    ) -> Order | SupportTicket | None:
        if order_id is not None:
            return await self._get_order(tenant_id, customer_id, order_id)
        if ticket_id is not None:
            return await self._get_ticket(tenant_id, customer_id, ticket_id)
        raise ValueError("an order_id or ticket_id is required")

    async def _get_order(self, tenant_id: str, customer_id: str, order_id: str) -> Order | None:
        async with self._session_factory() as session:
            model = await session.scalar(
                select(OrderModel).where(
                    OrderModel.tenant_id == tenant_id,
                    OrderModel.customer_id == customer_id,
                    OrderModel.order_id == order_id,
                )
            )
        if model is None:
            return None
        return Order(
            order_id=model.order_id,
            tenant_id=model.tenant_id,
            customer_id=model.customer_id,
            status=model.status,
            version=model.version,
            updated_at=model.updated_at,
        )

    async def _get_ticket(
        self, tenant_id: str, customer_id: str, ticket_id: str
    ) -> SupportTicket | None:
        async with self._session_factory() as session:
            model = await session.scalar(
                select(SupportTicketModel).where(
                    SupportTicketModel.tenant_id == tenant_id,
                    SupportTicketModel.customer_id == customer_id,
                    SupportTicketModel.ticket_id == ticket_id,
                )
            )
        if model is None:
            return None
        return SupportTicket(
            ticket_id=model.ticket_id,
            tenant_id=model.tenant_id,
            customer_id=model.customer_id,
            conversation_id=model.conversation_id,
            status=model.status,
            subject=model.subject,
            created_at=model.created_at,
        )


class PostgreSQLOrderReadAdapter:
    def __init__(self, delegate: PostgreSQLBusinessReadAdapter) -> None:
        self._delegate = delegate

    async def get_for_customer(
        self,
        *,
        tenant_id: str,
        customer_id: str,
        order_id: str,
    ) -> Order | None:
        result = await self._delegate.get_for_customer(
            tenant_id=tenant_id,
            customer_id=customer_id,
            order_id=order_id,
        )
        return result if isinstance(result, Order) else None


class PostgreSQLSupportTicketReadAdapter:
    def __init__(self, delegate: PostgreSQLBusinessReadAdapter) -> None:
        self._delegate = delegate

    async def get_for_customer(
        self,
        *,
        tenant_id: str,
        customer_id: str,
        ticket_id: str,
    ) -> SupportTicket | None:
        result = await self._delegate.get_for_customer(
            tenant_id=tenant_id,
            customer_id=customer_id,
            ticket_id=ticket_id,
        )
        return result if isinstance(result, SupportTicket) else None
