from datetime import datetime
from typing import Protocol

from app.domain.models import AuthenticatedPrincipal, ConversationLeadContext
from app.domain.models.presence import VisitorPresence


class VisitorPresenceStorePort(Protocol):
    async def upsert(self, presence: VisitorPresence) -> VisitorPresence: ...

    async def list_active(
        self,
        *,
        tenant_id: str,
        active_after: datetime,
    ) -> list[VisitorPresence]: ...


class ConversationVisitorContextPort(Protocol):
    async def authorize_conversation(
        self,
        *,
        principal: AuthenticatedPrincipal,
        conversation_id: str,
    ) -> bool: ...

    async def update_visitor_context(
        self,
        *,
        principal: AuthenticatedPrincipal,
        conversation_id: str,
        ip_address: str | None,
        country_code: str | None,
    ) -> None: ...


class ConversationLeadContextPort(Protocol):
    async def list_lead_contexts(
        self,
        *,
        tenant_id: str,
        conversation_ids: tuple[str, ...],
    ) -> tuple[ConversationLeadContext, ...]: ...
