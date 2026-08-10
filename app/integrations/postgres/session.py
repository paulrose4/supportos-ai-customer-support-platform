from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.application.tenant_context import (
    current_tenant_id,
    global_knowledge_access_enabled,
)


class DatabaseSessionManager:
    def __init__(
        self,
        database_url: str,
        *,
        pool_size: int = 10,
        max_overflow: int = 5,
        pool_timeout_seconds: float = 2.0,
        pool_recycle_seconds: int = 900,
        prepared_statement_cache_size: int = 100,
    ) -> None:
        self.engine: AsyncEngine = create_async_engine(
            database_url,
            connect_args={
                "prepared_statement_cache_size": prepared_statement_cache_size,
            },
            pool_pre_ping=True,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=pool_timeout_seconds,
            pool_recycle=pool_recycle_seconds,
        )
        event.listen(self.engine.sync_engine, "begin", _set_tenant_on_transaction_begin)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            yield session

    async def check(self) -> None:
        async with self.engine.connect() as connection:
            await connection.execute(text("select 1"))

    async def dispose(self) -> None:
        await self.engine.dispose()


def _set_tenant_on_transaction_begin(connection) -> None:  # type: ignore[no-untyped-def]
    tenant_id = current_tenant_id()
    if tenant_id is not None:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_id},
        )
    if global_knowledge_access_enabled():
        connection.execute(text("SELECT set_config('app.global_access', 'on', true)"))
