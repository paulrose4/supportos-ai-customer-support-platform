from uuid import uuid4

from redis.asyncio import Redis


class RedisKnowledgeSyncLease:
    _RELEASE_SCRIPT = """
    if redis.call('get', KEYS[1]) == ARGV[1] then
      return redis.call('del', KEYS[1])
    end
    return 0
    """

    def __init__(self, client: Redis) -> None:
        self._client = client

    async def acquire(self, *, tenant_id: str, ttl_seconds: int) -> str | None:
        token = str(uuid4())
        acquired = await self._client.set(
            f"knowledge-sync-lock:{tenant_id}", token, ex=ttl_seconds, nx=True
        )
        return token if acquired else None

    async def release(self, *, tenant_id: str, token: str) -> None:
        await self._client.eval(
            self._RELEASE_SCRIPT,
            1,
            f"knowledge-sync-lock:{tenant_id}",
            token,
        )
