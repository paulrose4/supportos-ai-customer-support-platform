from datetime import datetime
from typing import Protocol

from app.domain.models import (
    AgentResponse,
    AuthenticatedPrincipal,
    ConversationRoutingState,
    ConversationTurn,
    HandoffNotification,
    HandoffRequest,
    HumanSupportMessage,
    Order,
    SupportTicket,
)


class OrderRepositoryPort(Protocol):
    async def get_for_customer(
        self,
        *,
        tenant_id: str,
        customer_id: str,
        order_id: str,
    ) -> Order | None: ...


class SupportTicketRepositoryPort(Protocol):
    async def get_for_customer(
        self,
        *,
        tenant_id: str,
        customer_id: str,
        ticket_id: str,
    ) -> SupportTicket | None: ...


class HandoffPort(Protocol):
    async def create(self, request: HandoffRequest) -> HandoffRequest: ...


class HandoffNotificationPort(Protocol):
    async def send(self, notification: HandoffNotification) -> None: ...


class HandoffQueuePort(Protocol):
    async def list_for_tenant(
        self,
        *,
        tenant_id: str,
        status: str | None,
        limit: int,
    ) -> list[HandoffRequest]: ...

    async def list_page_for_tenant(
        self,
        *,
        tenant_id: str,
        status: str | None,
        limit: int,
        cursor: str | None,
        site_id: str | None,
        assigned_agent_id: str | None,
    ) -> tuple[list[HandoffRequest], str | None]: ...


class ConversationPersistencePort(Protocol):
    async def persist_exchange(
        self,
        *,
        principal: AuthenticatedPrincipal,
        user_message: str,
        response: AgentResponse,
        request_received_at: datetime | None = None,
        response_sent_at: datetime | None = None,
        request_route: str = "application",
        customer_confirmed_resolution: bool = False,
        visitor_ip_address: str | None = None,
        visitor_country_code: str | None = None,
    ) -> None: ...


class ConversationContextPort(Protocol):
    async def load_routing_state(
        self,
        *,
        principal: AuthenticatedPrincipal,
        conversation_id: str,
    ) -> ConversationRoutingState | None: ...

    async def load_human_messages(
        self,
        *,
        principal: AuthenticatedPrincipal,
        conversation_id: str,
        limit: int,
        after_id: int = 0,
    ) -> tuple[HumanSupportMessage, ...]: ...

    async def load_recent(
        self,
        *,
        principal: AuthenticatedPrincipal,
        conversation_id: str,
        limit: int,
    ) -> tuple[ConversationTurn, ...]: ...
