import asyncio
import json
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.domain.models import RealtimeEvent


class RedisPublicWidgetEventHub:
    """Conversation-scoped fanout for public Widget SSE connections."""

    _STREAM = "public-widget:events"

    def __init__(self, client: Redis, *, queue_size: int = 20) -> None:
        self._client = client
        self._queue_size = queue_size
        self._subscribers: dict[tuple[str, str, str], set[asyncio.Queue[RealtimeEvent]]] = (
            defaultdict(set)
        )
        self._lock = asyncio.Lock()
        self._listener: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._last_stream_id = "$"

    async def start(self) -> None:
        if self._listener is not None:
            return
        self._stopping.clear()
        try:
            latest = await self._client.xrevrange(self._STREAM, count=1)
        except RedisError:
            latest = []
        self._last_stream_id = _text(latest[0][0]) if latest else "0-0"
        self._listener = asyncio.create_task(self._listen(), name="public-widget-events")

    async def close(self) -> None:
        self._stopping.set()
        if self._listener is not None:
            self._listener.cancel()
            with suppress(asyncio.CancelledError):
                await self._listener
            self._listener = None

    @asynccontextmanager
    async def subscribe(
        self,
        *,
        tenant_id: str,
        site_id: str,
        conversation_id: str,
    ) -> AsyncIterator[asyncio.Queue[RealtimeEvent]]:
        await self.start()
        key = (tenant_id, site_id, conversation_id)
        queue: asyncio.Queue[RealtimeEvent] = asyncio.Queue(maxsize=self._queue_size)
        async with self._lock:
            self._subscribers[key].add(queue)
        try:
            yield queue
        finally:
            async with self._lock:
                subscribers = self._subscribers.get(key)
                if subscribers is not None:
                    subscribers.discard(queue)
                    if not subscribers:
                        self._subscribers.pop(key, None)

    async def _listen(self) -> None:
        while not self._stopping.is_set():
            try:
                messages = await self._client.xread(
                    {self._STREAM: self._last_stream_id}, block=1000, count=100
                )
            except RedisError:
                await asyncio.sleep(1)
                continue
            for _stream, entries in messages:
                for stream_id, fields in entries:
                    self._last_stream_id = _text(stream_id)
                    value = fields.get("event", fields.get(b"event"))
                    if value is None:
                        continue
                    try:
                        event = _decode(value)
                    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
                        continue
                    if event.resource_type == "conversation":
                        await self._fan_out(event)

    async def _fan_out(self, event: RealtimeEvent) -> None:
        async with self._lock:
            subscribers = tuple(
                queue
                for (tenant_id, site_id, conversation_id), queues in self._subscribers.items()
                if tenant_id == event.tenant_id
                and site_id == event.payload.get("site_id")
                and conversation_id == event.resource_id
                for queue in queues
            )
        for queue in subscribers:
            if queue.full():
                with suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(event)


def _decode(value: Any) -> RealtimeEvent:
    data = json.loads(value.decode() if isinstance(value, bytes) else value)
    occurred = data.get("occurred_at")
    return RealtimeEvent(
        event_id=str(data["event_id"]),
        tenant_id=str(data["tenant_id"]),
        event_type=str(data["event_type"]),
        resource_type=str(data["resource_type"]),
        resource_id=str(data["resource_id"]),
        payload=dict(data.get("payload") or {}),
        occurred_at=datetime.fromisoformat(occurred).astimezone(UTC) if occurred else None,
    )


def _text(value: Any) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)
