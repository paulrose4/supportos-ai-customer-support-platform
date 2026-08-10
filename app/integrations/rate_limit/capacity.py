import asyncio
import hashlib
import time
from secrets import token_urlsafe

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.domain.models.widget import WidgetCapacityLease

_ACQUIRE_SCRIPT = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', ARGV[1])
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[2]) then return 0 end
if redis.call('ZCARD', KEYS[2]) >= tonumber(ARGV[3]) then return 0 end
local fence = redis.call('INCR', KEYS[3])
local member = ARGV[5] .. ':' .. fence
redis.call('ZADD', KEYS[1], ARGV[4], member)
redis.call('ZADD', KEYS[2], ARGV[4], member)
redis.call('PEXPIRE', KEYS[1], ARGV[6])
redis.call('PEXPIRE', KEYS[2], ARGV[6])
redis.call('PEXPIRE', KEYS[3], ARGV[6])
return member
"""

_RELEASE_SCRIPT = """
redis.call('ZREM', KEYS[1], ARGV[1])
redis.call('ZREM', KEYS[2], ARGV[1])
return 1
"""


class RedisWidgetCapacityAdapter:
    def __init__(
        self,
        client: Redis,
        *,
        global_limit: int,
        site_limit: int,
        wait_seconds: float,
        lease_seconds: int,
    ) -> None:
        self._client = client
        self._global_limit = global_limit
        self._site_limit = min(site_limit, global_limit)
        self._wait_seconds = wait_seconds
        self._lease_ms = lease_seconds * 1000

    async def acquire(self, *, tenant_id: str, site_id: str) -> WidgetCapacityLease | None:
        site_key = _site_key(tenant_id, site_id)
        deadline = time.monotonic() + self._wait_seconds
        while True:
            now_ms = int(time.time() * 1000)
            try:
                member = await self._client.eval(
                    _ACQUIRE_SCRIPT,
                    3,
                    "widget-capacity:{shared}:global",
                    site_key,
                    "widget-capacity:{shared}:fence",
                    now_ms,
                    self._global_limit,
                    self._site_limit,
                    now_ms + self._lease_ms,
                    token_urlsafe(18),
                    self._lease_ms * 2,
                )
            except RedisError:
                return None
            if member and str(member) != "0":
                normalized_member = (
                    member.decode("utf-8") if isinstance(member, bytes) else str(member)
                )
                return WidgetCapacityLease(normalized_member, tenant_id, site_id)
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(0.05)

    async def release(self, lease: WidgetCapacityLease) -> None:
        try:
            await self._client.eval(
                _RELEASE_SCRIPT,
                2,
                "widget-capacity:{shared}:global",
                _site_key(lease.tenant_id, lease.site_id),
                lease.member,
            )
        except RedisError:
            return


class InMemoryWidgetCapacityAdapter:
    def __init__(self, *, global_limit: int, site_limit: int, wait_seconds: float) -> None:
        self._global = asyncio.Semaphore(global_limit)
        self._site_limit = min(site_limit, global_limit)
        self._sites: dict[tuple[str, str], asyncio.Semaphore] = {}
        self._wait_seconds = wait_seconds
        self._lock = asyncio.Lock()

    async def acquire(self, *, tenant_id: str, site_id: str) -> WidgetCapacityLease | None:
        async with self._lock:
            site = self._sites.setdefault((tenant_id, site_id), asyncio.Semaphore(self._site_limit))
        try:
            await asyncio.wait_for(self._global.acquire(), timeout=self._wait_seconds)
            try:
                await asyncio.wait_for(site.acquire(), timeout=self._wait_seconds)
            except TimeoutError:
                self._global.release()
                return None
        except TimeoutError:
            return None
        return WidgetCapacityLease(token_urlsafe(18), tenant_id, site_id)

    async def release(self, lease: WidgetCapacityLease) -> None:
        self._global.release()
        async with self._lock:
            site = self._sites.get((lease.tenant_id, lease.site_id))
        if site is not None:
            site.release()


def _site_key(tenant_id: str, site_id: str) -> str:
    digest = hashlib.sha256(f"{tenant_id}|{site_id}".encode()).hexdigest()
    return f"widget-capacity:{{shared}}:site:{digest}"
