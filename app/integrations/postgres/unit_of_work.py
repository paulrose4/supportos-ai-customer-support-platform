from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.integrations.postgres.repositories import (
    SqlAlchemyCustomerRepository,
    SqlAlchemyOrderRepository,
    SqlAlchemySupportTicketRepository,
)


class SqlAlchemyUnitOfWork:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self.session: AsyncSession | None = None
        self.customers: SqlAlchemyCustomerRepository | None = None
        self.orders: SqlAlchemyOrderRepository | None = None
        self.support_tickets: SqlAlchemySupportTicketRepository | None = None

    async def __aenter__(self) -> "SqlAlchemyUnitOfWork":
        self.session = self._session_factory()
        self.customers = SqlAlchemyCustomerRepository(self.session)
        self.orders = SqlAlchemyOrderRepository(self.session)
        self.support_tickets = SqlAlchemySupportTicketRepository(self.session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.session is None:
            return
        if exc_type is not None:
            await self.session.rollback()
        await self.session.close()

    async def commit(self) -> None:
        if self.session is None:
            raise RuntimeError("unit of work is not active")
        await self.session.commit()

    async def rollback(self) -> None:
        if self.session is None:
            raise RuntimeError("unit of work is not active")
        await self.session.rollback()
