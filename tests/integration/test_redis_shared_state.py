import asyncio
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from app.domain.models import RealtimeEvent, VisitorPresence
from app.integrations.presence import RedisVisitorPresenceStore
from app.integrations.rate_limit import RedisWidgetRateLimitAdapter
from app.realtime import RedisRealtimeHub

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION_TESTS") != "1" or not os.getenv("TEST_REDIS_URL"),
        reason="set RUN_INTEGRATION_TESTS=1 with TEST_REDIS_URL",
    ),
]


async def test_redis_state_is_shared_across_api_instances() -> None:
    url = os.environ["TEST_REDIS_URL"]
    first_client = Redis.from_url(url, decode_responses=True)
    second_client = Redis.from_url(url, decode_responses=True)
    first_hub = RedisRealtimeHub(first_client)
    second_hub = RedisRealtimeHub(second_client)
    tenant_id = f"test-{uuid4()}"
    now = datetime.now(UTC)
    first_limit = RedisWidgetRateLimitAdapter(first_client, fingerprint_secret="test-secret")
    second_limit = RedisWidgetRateLimitAdapter(second_client, fingerprint_secret="test-secret")
    first_presence = RedisVisitorPresenceStore(first_client, ttl_seconds=60)
    second_presence = RedisVisitorPresenceStore(second_client, ttl_seconds=60)

    try:
        await first_hub.start()
        await second_hub.start()
        async with second_hub.subscribe(tenant_id) as queue:
            await first_hub.publish(
                RealtimeEvent(
                    event_id=str(uuid4()),
                    tenant_id=tenant_id,
                    event_type="conversation.updated",
                    resource_type="conversation",
                    resource_id="conversation-1",
                    occurred_at=now,
                )
            )
            received = await asyncio.wait_for(queue.get(), timeout=3)
        assert received.tenant_id == tenant_id

        assert await first_limit.consume(
            bucket=tenant_id,
            source_key="visitor",
            limit=1,
            window_seconds=60,
            occurred_at=now,
        )
        assert not await second_limit.consume(
            bucket=tenant_id,
            source_key="visitor",
            limit=1,
            window_seconds=60,
            occurred_at=now,
        )

        await first_presence.upsert(
            VisitorPresence(tenant_id, "site-a", "visitor-a", None, "/", now)
        )
        assert len(await second_presence.list_active(tenant_id=tenant_id, active_after=now)) == 1
    finally:
        await first_hub.close()
        await second_hub.close()
        async for key in first_client.scan_iter(match=f"*{tenant_id}*"):
            await first_client.delete(key)
        await first_client.aclose()
        await second_client.aclose()
