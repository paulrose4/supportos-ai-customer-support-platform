from unittest.mock import AsyncMock

import pytest
from redis.exceptions import RedisError

from app.domain.models import PublicWidgetSite, RealtimeEvent
from app.domain.ports import ChatModelRequest, ChatModelResult
from app.integrations.cache import (
    RedisPublicWidgetAccessCache,
    RedisPublishedWidgetCacheInvalidator,
)
from app.integrations.llm import RedisModelGateway
from app.integrations.rate_limit import (
    InMemoryWidgetCapacityAdapter,
    RedisWidgetCapacityAdapter,
)
from app.realtime import RedisPublicWidgetEventHub, RedisRealtimeHub


def _site() -> PublicWidgetSite:
    return PublicWidgetSite(
        public_widget_id="site_pub_abcdefghijklmnop",
        tenant_id="tenant-a",
        site_id="site-a",
        allowed_origins=("https://shop.example.com",),
        status="active",
        daily_message_limit=500,
    )


class SourceAccess:
    def __init__(self, site: PublicWidgetSite | None) -> None:
        self.site = site
        self.calls = 0

    async def get_public_site(self, *, public_widget_id: str) -> PublicWidgetSite | None:
        self.calls += 1
        if self.site is not None and public_widget_id == self.site.public_widget_id:
            return self.site
        return None

    async def admit_message(self, **_kwargs) -> bool:  # type: ignore[no-untyped-def]
        return True


class CacheRedis:
    def __init__(self, *, fail: bool = False) -> None:
        self.values: dict[str, bytes] = {}
        self.expirations: dict[str, int] = {}
        self.fail = fail

    async def get(self, key: str) -> bytes | None:
        if self.fail:
            raise RedisError("cache unavailable")
        return self.values.get(key)

    async def set(self, key: str, value: str, *, ex: int) -> None:
        if self.fail:
            raise RedisError("cache unavailable")
        self.values[key] = value.encode("utf-8")
        self.expirations[key] = ex

    async def delete(self, key: str) -> None:
        if self.fail:
            raise RedisError("cache unavailable")
        self.values.pop(key, None)
        self.expirations.pop(key, None)


@pytest.mark.asyncio
async def test_public_widget_cache_hits_and_negative_caches_trusted_records() -> None:
    redis = CacheRedis()
    source = SourceAccess(_site())
    cached = RedisPublicWidgetAccessCache(source, redis)  # type: ignore[arg-type]

    first = await cached.get_public_site(public_widget_id=_site().public_widget_id)
    second = await cached.get_public_site(public_widget_id=_site().public_widget_id)

    assert first == second == _site()
    assert source.calls == 1
    assert next(iter(redis.expirations.values())) == 10

    missing_source = SourceAccess(None)
    missing = RedisPublicWidgetAccessCache(missing_source, redis)  # type: ignore[arg-type]
    assert await missing.get_public_site(public_widget_id="site_pub_missingmissing12") is None
    assert await missing.get_public_site(public_widget_id="site_pub_missingmissing12") is None
    assert missing_source.calls == 1
    assert redis.expirations["public-widget:site:site_pub_missingmissing12"] == 5


@pytest.mark.asyncio
async def test_public_widget_cache_fails_closed_to_bounded_database_source() -> None:
    source = SourceAccess(_site())
    cached = RedisPublicWidgetAccessCache(
        source,
        CacheRedis(fail=True),  # type: ignore[arg-type]
        fallback_concurrency=1,
    )

    assert await cached.get_public_site(public_widget_id=_site().public_widget_id) == _site()
    assert source.calls == 1


@pytest.mark.asyncio
async def test_published_widget_cache_invalidation_is_site_scoped_and_idempotent() -> None:
    redis = CacheRedis()
    key = "public-widget:site:site_pub_abcdefghijklmnop"
    redis.values[key] = b"cached"
    invalidator = RedisPublishedWidgetCacheInvalidator(redis)  # type: ignore[arg-type]

    await invalidator.invalidate(public_widget_id="site_pub_abcdefghijklmnop")
    await invalidator.invalidate(public_widget_id="site_pub_abcdefghijklmnop")

    assert key not in redis.values


@pytest.mark.asyncio
async def test_in_memory_capacity_enforces_site_limit_and_releases_slots() -> None:
    capacity = InMemoryWidgetCapacityAdapter(global_limit=2, site_limit=1, wait_seconds=0.01)

    first = await capacity.acquire(tenant_id="tenant-a", site_id="site-a")
    blocked = await capacity.acquire(tenant_id="tenant-a", site_id="site-a")
    other_site = await capacity.acquire(tenant_id="tenant-a", site_id="site-b")

    assert first is not None
    assert blocked is None
    assert other_site is not None
    await capacity.release(first)
    admitted_after_release = await capacity.acquire(tenant_id="tenant-a", site_id="site-a")
    assert admitted_after_release is not None
    await capacity.release(other_site)
    await capacity.release(admitted_after_release)


class CapacityRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    async def eval(self, *args):  # type: ignore[no-untyped-def]
        self.calls.append(args)
        return b"lease:1" if len(self.calls) == 1 else 1


@pytest.mark.asyncio
async def test_redis_capacity_uses_cluster_safe_keys_and_releases_exact_member() -> None:
    redis = CapacityRedis()
    capacity = RedisWidgetCapacityAdapter(
        redis,  # type: ignore[arg-type]
        global_limit=10,
        site_limit=2,
        wait_seconds=0.01,
        lease_seconds=90,
    )

    lease = await capacity.acquire(tenant_id="tenant-a", site_id="site-a")
    assert lease is not None
    assert lease.member == "lease:1"
    await capacity.release(lease)

    acquire_keys = redis.calls[0][2:5]
    assert all("{shared}" in str(key) for key in acquire_keys)
    assert redis.calls[1][-1] == "lease:1"


class EventRedis:
    def __init__(self) -> None:
        self.streams: list[str] = []

    async def xadd(self, stream: str, *_args, **_kwargs) -> None:  # type: ignore[no-untyped-def]
        self.streams.append(stream)


@pytest.mark.asyncio
async def test_realtime_publisher_routes_only_site_bound_conversations_to_widget_stream() -> None:
    redis = EventRedis()
    hub = RedisRealtimeHub(redis)  # type: ignore[arg-type]

    await hub.publish(
        RealtimeEvent(
            event_id="event-widget",
            tenant_id="tenant-a",
            event_type="conversation.agent_message_sent",
            resource_type="conversation",
            resource_id="conversation-a",
            payload={"site_id": "site-a"},
        )
    )
    await hub.publish(
        RealtimeEvent(
            event_id="event-admin",
            tenant_id="tenant-a",
            event_type="audit.updated",
            resource_type="audit",
            resource_id="audit-a",
        )
    )

    assert redis.streams == [
        "realtime:events",
        "public-widget:events",
        "realtime:events",
    ]


class ModelRedis:
    def __init__(self, admitted: int = 1) -> None:
        self.admitted = admitted
        self.calls: list[tuple[object, ...]] = []

    async def eval(self, *args):  # type: ignore[no-untyped-def]
        self.calls.append(args)
        return self.admitted


class StubModel:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    async def generate(self, request: ChatModelRequest) -> ChatModelResult:
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider unavailable")
        return ChatModelResult(text=str(request.messages[-1]["content"]), model="stub")


def _gateway(model: StubModel, redis: ModelRedis, *, threshold: int = 2) -> RedisModelGateway:
    return RedisModelGateway(
        model,
        redis,  # type: ignore[arg-type]
        requests_per_minute=100,
        tokens_per_minute=100_000,
        reserved_output_tokens=100,
        circuit_failure_threshold=threshold,
        circuit_recovery_seconds=30,
    )


@pytest.mark.asyncio
async def test_model_gateway_enforces_budget_and_opens_circuit() -> None:
    request = ChatModelRequest(messages=({"role": "user", "content": "hello"},))
    denied_model = StubModel()
    denied = _gateway(denied_model, ModelRedis(admitted=0))
    with pytest.raises(RuntimeError, match="budget is exhausted"):
        await denied.generate(request)
    assert denied_model.calls == 0

    failing_model = StubModel(fail=True)
    circuit = _gateway(failing_model, ModelRedis(), threshold=2)
    with pytest.raises(RuntimeError, match="provider unavailable"):
        await circuit.generate(request)
    with pytest.raises(RuntimeError, match="provider unavailable"):
        await circuit.generate(request)
    with pytest.raises(RuntimeError, match="circuit is open"):
        await circuit.generate(request)
    assert failing_model.calls == 2


@pytest.mark.asyncio
async def test_public_widget_events_require_exact_tenant_site_and_conversation() -> None:
    hub = RedisPublicWidgetEventHub(AsyncMock())  # type: ignore[arg-type]
    hub.start = AsyncMock()  # type: ignore[method-assign]
    event = RealtimeEvent(
        event_id="event-1",
        tenant_id="tenant-a",
        event_type="conversation.agent_message_sent",
        resource_type="conversation",
        resource_id="conversation-a",
        payload={"site_id": "site-a"},
    )

    async with (
        hub.subscribe(
            tenant_id="tenant-a", site_id="site-a", conversation_id="conversation-a"
        ) as exact,
        hub.subscribe(
            tenant_id="tenant-a", site_id="site-b", conversation_id="conversation-a"
        ) as wrong_site,
        hub.subscribe(
            tenant_id="tenant-b", site_id="site-a", conversation_id="conversation-a"
        ) as wrong_tenant,
        hub.subscribe(
            tenant_id="tenant-a", site_id="site-a", conversation_id="conversation-b"
        ) as wrong_conversation,
    ):
        await hub._fan_out(event)

        assert await exact.get() == event
        assert wrong_site.empty()
        assert wrong_tenant.empty()
        assert wrong_conversation.empty()
