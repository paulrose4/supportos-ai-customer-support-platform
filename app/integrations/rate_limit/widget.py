import asyncio
import hashlib
import hmac
from datetime import datetime


class InMemoryWidgetRateLimitAdapter:
    def __init__(self, *, fingerprint_secret: str) -> None:
        self._secret = fingerprint_secret.encode("utf-8")
        self._entries: dict[tuple[str, str, int], int] = {}
        self._lock = asyncio.Lock()

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
            self._secret,
            f"{bucket}|{source_key}".encode(),
            hashlib.sha256,
        ).hexdigest()
        window = int(occurred_at.timestamp()) // window_seconds
        key = (bucket, fingerprint, window)
        async with self._lock:
            count = self._entries.get(key, 0)
            if count >= limit:
                return False
            self._entries[key] = count + 1
            if len(self._entries) > 10_000:
                self._entries = {
                    entry_key: value
                    for entry_key, value in self._entries.items()
                    if entry_key[2] >= window - 2
                }
        return True
