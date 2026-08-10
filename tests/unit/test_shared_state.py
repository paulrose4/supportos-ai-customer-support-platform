from datetime import UTC, datetime, timedelta

import pytest

from app.domain.models import VisitorPresence
from app.integrations.presence import RedisVisitorPresenceStore
from app.integrations.rate_limit import RedisWidgetRateLimitAdapter
from app.observability import PrometheusRequestMetrics


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str | int] = {}
        self.expirations: dict[str, int] = {}
        self.sorted_sets: dict[str, dict[str, float]] = {}

    async def set(self, key: str, value: str, *, ex: int) -> None:
        self.values[key] = value
        self.expirations[key] = ex

    async def get(self, key: str) -> str | None:
        value = self.values.get(key)
        return str(value) if value is not None else None

    async def mget(self, keys: list[str]) -> list[str | None]:
        return [await self.get(key) for key in keys]

    async def zadd(self, key: str, mapping: dict[str, float]) -> None:
        self.sorted_sets.setdefault(key, {}).update(mapping)

    async def zrangebyscore(
        self,
        key: str,
        minimum: float,
        maximum: str,
    ) -> list[str]:
        del maximum
        return [
            member
            for member, score in self.sorted_sets.get(key, {}).items()
            if score >= float(minimum)
        ]

    async def zremrangebyscore(self, key: str, minimum: str, maximum: float) -> None:
        del minimum
        values = self.sorted_sets.get(key, {})
        for member in [member for member, score in values.items() if score <= maximum]:
            values.pop(member, None)

    async def zrem(self, key: str, *members: str) -> None:
        values = self.sorted_sets.get(key, {})
        for member in members:
            values.pop(member, None)

    def pipeline(self, *, transaction: bool) -> "FakePipeline":
        assert transaction is True
        return FakePipeline(self)

    async def scan_iter(self, *, match: str):  # type: ignore[no-untyped-def]
        prefix = match.removesuffix("*")
        for key in tuple(self.values):
            if key.startswith(prefix):
                yield key

    async def incr(self, key: str) -> int:
        value = int(self.values.get(key, 0)) + 1
        self.values[key] = value
        return value

    async def expire(self, key: str, seconds: int) -> None:
        self.expirations[key] = seconds


class FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self.redis = redis
        self.operations: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    async def __aenter__(self) -> "FakePipeline":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def watch(self, _key: str) -> None:
        return None

    async def get(self, key: str) -> str | None:
        return await self.redis.get(key)

    def multi(self) -> None:
        return None

    def set(self, key: str, value: str, *, ex: int) -> None:
        self.operations.append(("set", (key, value), {"ex": ex}))

    def zadd(self, key: str, mapping: dict[str, float]) -> None:
        self.operations.append(("zadd", (key, mapping), {}))

    def expire(self, key: str, seconds: int) -> None:
        self.operations.append(("expire", (key, seconds), {}))

    async def execute(self) -> None:
        for method, args, kwargs in self.operations:
            await getattr(self.redis, method)(*args, **kwargs)


@pytest.mark.asyncio
async def test_redis_rate_limit_is_shared_between_adapter_instances() -> None:
    redis = FakeRedis()
    first = RedisWidgetRateLimitAdapter(redis, fingerprint_secret="secret")  # type: ignore[arg-type]
    second = RedisWidgetRateLimitAdapter(redis, fingerprint_secret="secret")  # type: ignore[arg-type]
    now = datetime.now(UTC)

    assert await first.consume(
        bucket="chat", source_key="visitor", limit=2, window_seconds=60, occurred_at=now
    )
    assert await second.consume(
        bucket="chat", source_key="visitor", limit=2, window_seconds=60, occurred_at=now
    )
    assert not await first.consume(
        bucket="chat", source_key="visitor", limit=2, window_seconds=60, occurred_at=now
    )


@pytest.mark.asyncio
async def test_redis_presence_is_tenant_scoped_and_has_ttl() -> None:
    redis = FakeRedis()
    store = RedisVisitorPresenceStore(redis, ttl_seconds=90)  # type: ignore[arg-type]
    now = datetime.now(UTC)
    await store.upsert(
        VisitorPresence(
            "tenant-a",
            "site-a",
            "visitor-a",
            None,
            "/faq",
            now,
            page_kind="support",
        )
    )
    await store.upsert(VisitorPresence("tenant-b", "site-b", "visitor-b", None, "/", now))

    items = await store.list_active(tenant_id="tenant-a", active_after=now - timedelta(seconds=1))

    assert [item.visitor_id for item in items] == ["visitor-a"]
    assert items[0].page_kind == "support"
    assert redis.expirations["presence:item:tenant-a:site-a:visitor-a"] == 90
    assert "presence:item:tenant-a:site-a:visitor-a" in redis.sorted_sets["presence:index:tenant-a"]


def test_prometheus_metrics_and_latency_quantiles_are_exported() -> None:
    metrics = PrometheusRequestMetrics()
    for duration in (0.1, 0.2, 0.4, 1.0):
        metrics.record(
            method="GET",
            route="/v1/items/{item_id}",
            status_code=200,
            duration_seconds=duration,
        )
    metrics.record_agent_response(
        dialogue_act="answer",
        response_kind="answer",
        generation_count=2,
        repaired=True,
        fallback=False,
    )
    metrics.record_agent_response(
        dialogue_act="clarify",
        response_kind="clarification",
        generation_count=0,
        repaired=False,
        fallback=True,
    )
    metrics.record_widget_admission(outcome="admitted")
    metrics.record_widget_cache(outcome="miss")
    metrics.adjust_widget_chat_in_flight(1)
    metrics.adjust_widget_chat_queue(1)
    metrics.adjust_widget_sse_connections(1)
    metrics.record_agent_stage(
        stage="answer_knowledge",
        outcome="success",
        duration_seconds=0.25,
    )

    snapshot = metrics.snapshot()
    rendered = metrics.render().decode()

    assert snapshot["p50_latency_ms"] == pytest.approx(400)
    assert snapshot["p95_latency_ms"] == pytest.approx(1000)
    assert 'route="/v1/items/{item_id}"' in rendered
    assert (
        'http_responses_total{method="GET",route="/v1/items/{item_id}",status_code="200"} 4.0'
        in rendered
    )
    assert snapshot["agent_response_count"] == 2
    assert snapshot["agent_repair_count"] == 1
    assert snapshot["agent_fallback_count"] == 1
    assert snapshot["agent_generation_count"] == 2
    assert snapshot["agent_stage_answer_knowledge_p95_ms"] == pytest.approx(250)
    assert snapshot["widget_chat_in_flight"] == 1
    assert snapshot["widget_chat_queue"] == 1
    assert snapshot["widget_sse_connections"] == 1
    assert 'agent_responses_total{dialogue_act="answer",response_kind="answer"} 1.0' in rendered
    assert "agent_response_repairs_total 1.0" in rendered
    assert "agent_response_fallbacks_total 1.0" in rendered
    assert (
        'agent_stage_duration_seconds_count{outcome="success",stage="answer_knowledge"} 1.0'
        in rendered
    )
    assert 'widget_admission_total{outcome="admitted"} 1.0' in rendered
    assert 'widget_site_cache_total{outcome="miss"} 1.0' in rendered
    assert "widget_chat_in_flight 1.0" in rendered
    assert "widget_chat_queue_depth 1.0" in rendered
    assert "widget_sse_connections 1.0" in rendered
    assert "tenant" not in rendered
