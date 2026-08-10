import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.domain.models import RealtimeEvent


class InMemoryRealtimeHub:
    def __init__(self, *, queue_size: int = 200) -> None:
        self._queue_size = queue_size
        self._subscribers: dict[str, set[asyncio.Queue[RealtimeEvent]]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def publish(self, event: RealtimeEvent) -> None:
        async with self._lock:
            subscribers = tuple(self._subscribers.get(event.tenant_id, ()))
        for queue in subscribers:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)

    @asynccontextmanager
    async def subscribe(self, tenant_id: str) -> AsyncIterator[asyncio.Queue[RealtimeEvent]]:
        queue: asyncio.Queue[RealtimeEvent] = asyncio.Queue(maxsize=self._queue_size)
        async with self._lock:
            self._subscribers[tenant_id].add(queue)
        try:
            yield queue
        finally:
            async with self._lock:
                subscribers = self._subscribers.get(tenant_id)
                if subscribers is not None:
                    subscribers.discard(queue)
                    if not subscribers:
                        self._subscribers.pop(tenant_id, None)
