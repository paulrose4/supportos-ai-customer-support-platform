from dataclasses import dataclass
from datetime import datetime

from app.domain.models import AgentResponse, AuthenticatedPrincipal, HumanSupportMessage


@dataclass(frozen=True, slots=True)
class HandleChatCommand:
    conversation_id: str | None
    message: str
    principal: AuthenticatedPrincipal
    page_path: str = "/"
    request_received_at: datetime | None = None
    request_route: str = "application"
    visitor_ip_address: str | None = None
    visitor_country_code: str | None = None


@dataclass(frozen=True, slots=True)
class HandleChatResult:
    response: AgentResponse


@dataclass(frozen=True, slots=True)
class ListHumanMessagesQuery:
    principal: AuthenticatedPrincipal
    conversation_id: str
    limit: int = 20
    after_cursor: str | None = None


@dataclass(frozen=True, slots=True)
class ListHumanMessagesResult:
    items: tuple[HumanSupportMessage, ...]
    conversation_status: str | None = None
    handoff_active: bool = False
    next_cursor: str | None = None


@dataclass(frozen=True, slots=True)
class GetPublicConversationStateQuery:
    principal: AuthenticatedPrincipal
    conversation_id: str


@dataclass(frozen=True, slots=True)
class PublicConversationStateResult:
    exists: bool
    conversation_status: str | None = None
    handoff_active: bool = False
    handoff_id: str | None = None
    last_human_cursor: str | None = None
