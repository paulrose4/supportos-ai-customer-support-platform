from typing import Protocol

from app.domain.models import (
    AuthenticatedPrincipal,
    ConversationMemoryContext,
    ConversationWorkingMemory,
)


class ConversationMemoryPort(Protocol):
    async def load(
        self,
        *,
        principal: AuthenticatedPrincipal,
        conversation_id: str,
        query: str = "",
    ) -> ConversationMemoryContext: ...

    async def save(
        self,
        *,
        principal: AuthenticatedPrincipal,
        conversation_id: str,
        working: ConversationWorkingMemory,
        trace_id: str,
        used_memory_ids: tuple[str, ...] = (),
    ) -> None: ...
