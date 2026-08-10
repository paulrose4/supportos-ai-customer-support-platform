import asyncio
from contextlib import AbstractAsyncContextManager
from typing import Protocol

from app.domain.models import RealtimeEvent


class EventPublisherPort(Protocol):
    async def publish(self, event: RealtimeEvent) -> None: ...


class RealtimeHubPort(EventPublisherPort, Protocol):
    def subscribe(
        self, tenant_id: str
    ) -> AbstractAsyncContextManager[asyncio.Queue[RealtimeEvent]]: ...


class PublicWidgetEventPort(Protocol):
    def subscribe(
        self,
        *,
        tenant_id: str,
        site_id: str,
        conversation_id: str,
    ) -> AbstractAsyncContextManager[asyncio.Queue[RealtimeEvent]]: ...
