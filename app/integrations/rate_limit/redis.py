import hashlib
import hmac
from datetime import datetime

from redis.asyncio import Redis


class RedisWidgetRateLimitAdapter:
    """Atomic fixed-window limiter shared by every API worker."""

    def __init__(self, client: Redis, *, fingerprint_secret: str) -> None:
        self._client = client
        self._secret = fingerprint_secret.encode("utf-8")

    async def consume(
        self,
        *,
        bucket: str,
        source_key: str,
        limit: int,
        window_seconds: int,
        occurred_at: datetime,
    ) -> bool:
        if limit <= 0 or window_seconds <= 0:
            return False
        fingerprint = hmac.new(
            self._secret, f"{bucket}|{source_key}".encode(), hashlib.sha256
        ).hexdigest()
        window = int(occurred_at.timestamp()) // window_seconds
        key = f"ratelimit:{bucket}:{fingerprint}:{window}"
        count = await self._client.incr(key)
        if count == 1:
            await self._client.expire(key, window_seconds + 1)
        return int(count) <= limit
