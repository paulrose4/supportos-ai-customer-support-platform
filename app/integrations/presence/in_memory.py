import asyncio
from datetime import datetime

from app.domain.models import VisitorPresence
from app.integrations.presence.mapping import merge_presence


class InMemoryVisitorPresenceStore:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str, str], VisitorPresence] = {}
        self._lock = asyncio.Lock()

    async def upsert(self, presence: VisitorPresence) -> VisitorPresence:
        key = (presence.tenant_id, presence.site_id, presence.visitor_id)
        async with self._lock:
            current = self._items.get(key)
            merged = merge_presence(current, presence)
            self._items[key] = merged
        return merged

    async def list_active(
        self,
        *,
        tenant_id: str,
        active_after: datetime,
    ) -> list[VisitorPresence]:
        async with self._lock:
            stale_keys = [
                key for key, item in self._items.items() if item.last_seen_at < active_after
            ]
            for key in stale_keys:
                self._items.pop(key, None)
            items = [
                item
                for item in self._items.values()
                if item.tenant_id == tenant_id and item.last_seen_at >= active_after
            ]
        return sorted(items, key=lambda item: item.last_seen_at, reverse=True)
