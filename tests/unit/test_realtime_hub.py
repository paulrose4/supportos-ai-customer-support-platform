import asyncio
from datetime import UTC, datetime

import pytest

from app.domain.models import RealtimeEvent
from app.realtime import InMemoryRealtimeHub


@pytest.mark.asyncio
async def test_realtime_hub_isolates_tenants() -> None:
    hub = InMemoryRealtimeHub()
    event = RealtimeEvent(
        event_id="event-1",
        tenant_id="tenant-a",
        event_type="conversation.updated",
        resource_type="conversation",
        resource_id="conversation-1",
        occurred_at=datetime.now(UTC),
    )

    async with hub.subscribe("tenant-a") as tenant_a, hub.subscribe("tenant-b") as tenant_b:
        await hub.publish(event)

        assert await asyncio.wait_for(tenant_a.get(), timeout=0.1) == event
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(tenant_b.get(), timeout=0.01)
