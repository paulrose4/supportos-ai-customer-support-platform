from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import Order, SupportTicket
from app.integrations.postgres.models import OrderModel, SupportTicketModel


class SqlAlchemyOrderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_customer(
        self,
        *,
        tenant_id: str,
        customer_id: str,
        order_id: str,
    ) -> Order | None:
        statement = select(OrderModel).where(
            OrderModel.tenant_id == tenant_id,
            OrderModel.customer_id == customer_id,
            OrderModel.order_id == order_id,
        )
        model = await self._session.scalar(statement)
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


class SqlAlchemySupportTicketRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_customer(
        self,
        *,
        tenant_id: str,
        customer_id: str,
        ticket_id: str,
    ) -> SupportTicket | None:
        statement = select(SupportTicketModel).where(
            SupportTicketModel.tenant_id == tenant_id,
            SupportTicketModel.customer_id == customer_id,
            SupportTicketModel.ticket_id == ticket_id,
        )
        model = await self._session.scalar(statement)
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
