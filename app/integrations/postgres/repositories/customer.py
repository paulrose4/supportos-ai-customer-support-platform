from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import Customer
from app.integrations.postgres.models import CustomerModel


class SqlAlchemyCustomerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, *, tenant_id: str, entity_id: str) -> Customer | None:
        statement = select(CustomerModel).where(
            CustomerModel.tenant_id == tenant_id,
            CustomerModel.customer_id == entity_id,
        )
        model = await self._session.scalar(statement)
        if model is None:
            return None
        return Customer(
            customer_id=model.customer_id,
            tenant_id=model.tenant_id,
            display_name=model.display_name,
        )

    async def add(self, *, tenant_id: str, entity: Customer) -> None:
        if entity.tenant_id != tenant_id:
            raise ValueError("customer tenant does not match repository tenant")
        self._session.add(
            CustomerModel(
                tenant_id=tenant_id,
                customer_id=entity.customer_id,
                display_name=entity.display_name,
            )
        )
