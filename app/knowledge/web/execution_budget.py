import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class WebSyncPageLease:
    """An idempotent lease for one process-wide page execution slot."""

    __slots__ = ("_semaphore", "_released")

    def __init__(self, semaphore: asyncio.Semaphore) -> None:
        self._semaphore = semaphore
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._semaphore.release()

    async def __aenter__(self) -> "WebSyncPageLease":
        return self

    async def __aexit__(self, *_args: object) -> None:
        self.release()


class WebSyncExecutionBudget:
    """Process-wide resource limits shared by every tenant execution."""

    def __init__(self, *, page_slots: int, finalization_slots: int) -> None:
        if not 1 <= page_slots <= 64:
            raise ValueError("website sync page slots must be between 1 and 64")
        if not 1 <= finalization_slots <= 16:
            raise ValueError("website sync finalization slots must be between 1 and 16")
        self._page_slots = asyncio.Semaphore(page_slots)
        self._finalization_slots = asyncio.Semaphore(finalization_slots)

    @asynccontextmanager
    async def page(self) -> AsyncIterator[None]:
        lease = await self.acquire_page()
        try:
            yield
        finally:
            lease.release()

    async def acquire_page(self) -> WebSyncPageLease:
        """Reserve a page slot before taking a durable page lease.

        Claiming the database row only after this method returns prevents a
        page from sitting in ``fetching`` while it waits behind another
        tenant for scarce embedding/network capacity.
        """

        await self._page_slots.acquire()
        return WebSyncPageLease(self._page_slots)

    @asynccontextmanager
    async def finalization(self) -> AsyncIterator[None]:
        async with self._finalization_slots:
            yield
