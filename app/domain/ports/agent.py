from typing import Protocol

from app.domain.models import (
    AgentResponse,
    AuthenticatedPrincipal,
    ConversationMemoryContext,
    ConversationTurn,
)


class AgentRunnerPort(Protocol):
    async def run(
        self,
        *,
        conversation_id: str,
        message: str,
        principal: AuthenticatedPrincipal,
        history: tuple[ConversationTurn, ...] = (),
        page_path: str = "/",
        memory: ConversationMemoryContext | None = None,
    ) -> AgentResponse: ...
