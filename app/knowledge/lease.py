import asyncio
from uuid import uuid4


class InMemoryKnowledgeSyncLease:
    def __init__(self) -> None:
        self._leases: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, *, tenant_id: str, ttl_seconds: int) -> str | None:
        del ttl_seconds
        async with self._lock:
            if tenant_id in self._leases:
                return None
            token = str(uuid4())
            self._leases[tenant_id] = token
            return token

    async def release(self, *, tenant_id: str, token: str) -> None:
        async with self._lock:
            if self._leases.get(tenant_id) == token:
                self._leases.pop(tenant_id, None)
