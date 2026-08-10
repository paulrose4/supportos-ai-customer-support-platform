import asyncio
import json
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis

from app.domain.models import RealtimeEvent


class RedisRealtimeHub:
    """Redis Stream transport with local bounded queues for WebSocket clients."""

    _STREAM = "realtime:events"
    _PUBLIC_WIDGET_STREAM = "public-widget:events"

    def __init__(self, client: Redis, *, queue_size: int = 200) -> None:
        self._client = client
        self._queue_size = queue_size
        self._subscribers: dict[str, set[asyncio.Queue[RealtimeEvent]]] = defaultdict(set)
        self._lock = asyncio.Lock()
        self._listener: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._last_stream_id = "$"

    async def start(self) -> None:
        if self._listener is None:
            self._stopping.clear()
            latest = await self._client.xrevrange(self._STREAM, count=1)
            self._last_stream_id = latest[0][0] if latest else "0-0"
            self._listener = asyncio.create_task(self._listen(), name="realtime-redis-listener")

    async def close(self) -> None:
        self._stopping.set()
        if self._listener is not None:
            self._listener.cancel()
            with suppress(asyncio.CancelledError):
                await self._listener
            self._listener = None

    async def publish(self, event: RealtimeEvent) -> None:
        encoded = _encode(event)
        await self._client.xadd(
            self._STREAM,
            {"event": encoded},
            maxlen=100_000,
            approximate=True,
        )
        if event.resource_type == "conversation" and event.payload.get("site_id"):
            await self._client.xadd(
                self._PUBLIC_WIDGET_STREAM,
                {"event": encoded},
                maxlen=100_000,
                approximate=True,
            )

    @asynccontextmanager
    async def subscribe(self, tenant_id: str) -> AsyncIterator[asyncio.Queue[RealtimeEvent]]:
        await self.start()
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

    async def _listen(self) -> None:
        while not self._stopping.is_set():
            messages = await self._client.xread(
                {self._STREAM: self._last_stream_id}, block=1000, count=100
            )
            for _stream, entries in messages:
                for stream_id, fields in entries:
                    self._last_stream_id = stream_id
                    value = fields.get("event")
                    if value is None:
                        continue
                    try:
                        event = _decode(value)
                    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
                        continue
                    async with self._lock:
                        subscribers = tuple(self._subscribers.get(event.tenant_id, ()))
                    for queue in subscribers:
                        if queue.full():
                            with suppress(asyncio.QueueEmpty):
                                queue.get_nowait()
                        queue.put_nowait(event)


def _encode(event: RealtimeEvent) -> str:
    return json.dumps(
        {
            "event_id": event.event_id,
            "tenant_id": event.tenant_id,
            "event_type": event.event_type,
            "resource_type": event.resource_type,
            "resource_id": event.resource_id,
            "payload": event.payload,
            "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
        },
        separators=(",", ":"),
    )


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
