from datetime import datetime
from typing import Protocol


class AutoResolutionPort(Protocol):
    async def count_due(self, *, evaluated_at: datetime) -> int: ...

    async def resolve_due(self, *, evaluated_at: datetime, limit: int) -> int: ...
