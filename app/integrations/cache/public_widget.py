import asyncio
import hashlib
import json
from dataclasses import asdict
from datetime import datetime

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.application.tenant_context import bind_tenant
from app.domain.models import PublicWidgetSite
from app.domain.ports.metrics import RequestMetricsPort
from app.domain.ports.widget import PublicWidgetAccessPort
from app.integrations.postgres.customer_experience import widget_config_from_payload


class RedisPublicWidgetAccessCache:
    """Caches only trusted site records produced by the PostgreSQL adapter."""

    _MISSING = "__missing__"

    def __init__(
        self,
        source: PublicWidgetAccessPort,
        client: Redis,
        *,
        ttl_seconds: int = 10,
        negative_ttl_seconds: int = 5,
        fallback_concurrency: int = 10,
        metrics: RequestMetricsPort | None = None,
    ) -> None:
        self._source = source
        self._client = client
        self._ttl = ttl_seconds
        self._negative_ttl = negative_ttl_seconds
        self._fallback = asyncio.Semaphore(fallback_concurrency)
        self._locks = tuple(asyncio.Lock() for _ in range(64))
        self._metrics = metrics

    async def get_public_site(self, *, public_widget_id: str) -> PublicWidgetSite | None:
        key = f"public-widget:site:{public_widget_id}"
        cached = await self._read(key)
        if cached is not None:
            if self._metrics is not None:
                self._metrics.record_widget_cache(outcome="hit")
            site = _decode_site(cached)
            if site is not None:
                bind_tenant(site.tenant_id)
            return site
        lock = await self._lock_for(public_widget_id)
        if self._metrics is not None:
            self._metrics.record_widget_cache(outcome="miss")
        async with lock:
            cached = await self._read(key)
            if cached is not None:
                site = _decode_site(cached)
                if site is not None:
                    bind_tenant(site.tenant_id)
                return site
            async with self._fallback:
                site = await self._source.get_public_site(public_widget_id=public_widget_id)
            await self._write(key, site)
            return site

    async def admit_message(
        self,
        *,
        tenant_id: str,
        site_id: str,
        request_id: str,
        occurred_at: datetime,
    ) -> bool:
        return await self._source.admit_message(
            tenant_id=tenant_id,
            site_id=site_id,
            request_id=request_id,
            occurred_at=occurred_at,
        )

    async def _read(self, key: str) -> str | None:
        try:
            value = await self._client.get(key)
        except RedisError:
            return None
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value) if value is not None else None

    async def _write(self, key: str, site: PublicWidgetSite | None) -> None:
        try:
            if site is None:
                await self._client.set(key, self._MISSING, ex=self._negative_ttl)
            else:
                await self._client.set(key, json.dumps(asdict(site)), ex=self._ttl)
        except RedisError:
            return

    async def _lock_for(self, public_widget_id: str) -> asyncio.Lock:
        digest = hashlib.sha256(public_widget_id.encode("utf-8")).digest()
        return self._locks[int.from_bytes(digest[:2], "big") % len(self._locks)]


class RedisPublishedWidgetCacheInvalidator:
    def __init__(self, client: Redis) -> None:
        self._client = client

    async def invalidate(self, *, public_widget_id: str) -> None:
        await self._client.delete(f"public-widget:site:{public_widget_id}")


def _decode_site(value: str) -> PublicWidgetSite | None:
    if value == RedisPublicWidgetAccessCache._MISSING:
        return None
    try:
        payload = json.loads(value)
        config = payload.get("widget_config")
        return PublicWidgetSite(
            public_widget_id=str(payload["public_widget_id"]),
            tenant_id=str(payload["tenant_id"]),
            site_id=str(payload["site_id"]),
            allowed_origins=tuple(str(item) for item in payload["allowed_origins"]),
            status=str(payload["status"]),
            daily_message_limit=int(payload["daily_message_limit"]),
            primary_language=str(payload.get("primary_language", "en")),
            widget_config=widget_config_from_payload(config) if config else None,
            base_url=str(payload.get("base_url", "")),
            widget_config_version=str(payload.get("widget_config_version", "site-identity-v1")),
            verification_status=str(payload.get("verification_status", "verified")),
            auth_version=int(payload.get("auth_version", 1)),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
