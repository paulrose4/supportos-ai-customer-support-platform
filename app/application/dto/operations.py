from dataclasses import dataclass

from app.domain.models import (
    AuthenticatedPrincipal,
    CannedReply,
    ConversationPriority,
    ConversationStatus,
    ConversationWorkspace,
    CustomerDirectoryItem,
    CustomerMemoryItem,
    InboxConversation,
    InboxCounts,
    MemoryCandidate,
    MemoryKind,
    OwnershipMode,
    ResolutionEpisode,
    SupportAgentOption,
    SupportQueue,
    SupportSite,
)


@dataclass(frozen=True, slots=True)
class ListInboxQuery:
    principal: AuthenticatedPrincipal
    status: ConversationStatus | None = None
    ownership_mode: OwnershipMode | None = None
    site_id: str | None = None
    mine_only: bool = False
    queue_id: str | None = None
    priority: ConversationPriority | None = None
    tag: str | None = None
    unread_only: bool = False
    limit: int = 50
    cursor: str | None = None
    search: str | None = None
    sla_risk_only: bool = False
    priority_risk_only: bool = False


@dataclass(frozen=True, slots=True)
class ListInboxResult:
    items: tuple[InboxConversation, ...]
    next_cursor: str | None = None
    total: int | None = None


@dataclass(frozen=True, slots=True)
class InboxCountsResult:
    counts: InboxCounts


@dataclass(frozen=True, slots=True)
class ListCustomersQuery:
    principal: AuthenticatedPrincipal
    site_id: str | None = None
    search: str | None = None
    limit: int = 100
    cursor: str | None = None


@dataclass(frozen=True, slots=True)
class ListCustomersResult:
    items: tuple[CustomerDirectoryItem, ...]
    next_cursor: str | None = None
    total: int | None = None


@dataclass(frozen=True, slots=True)
class ListCustomerConversationsQuery:
    principal: AuthenticatedPrincipal
    customer_id: str
    site_id: str | None = None
    limit: int = 100
    cursor: str | None = None


@dataclass(frozen=True, slots=True)
class ListSitesQuery:
    principal: AuthenticatedPrincipal


@dataclass(frozen=True, slots=True)
class ListSitesResult:
    items: tuple[SupportSite, ...]


@dataclass(frozen=True, slots=True)
class GetConversationQuery:
    principal: AuthenticatedPrincipal
    conversation_id: str


@dataclass(frozen=True, slots=True)
class ConversationActionCommand:
    principal: AuthenticatedPrincipal
    conversation_id: str
    idempotency_key: str
    expected_routing_version: int | None = None


@dataclass(frozen=True, slots=True)
class SendAgentMessageCommand:
    principal: AuthenticatedPrincipal
    conversation_id: str
    content: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class AddInternalNoteCommand:
    principal: AuthenticatedPrincipal
    conversation_id: str
    content: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class UpdateConversationRoutingCommand:
    principal: AuthenticatedPrincipal
    conversation_id: str
    assigned_agent_id: str | None
    queue_id: str | None
    priority: ConversationPriority
    tags: tuple[str, ...]
    idempotency_key: str
    expected_routing_version: int | None = None


@dataclass(frozen=True, slots=True)
class CreateManualHandoffCommand:
    principal: AuthenticatedPrincipal
    conversation_id: str
    summary: str
    queue_id: str | None
    priority: ConversationPriority
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ListSupportConfigurationQuery:
    principal: AuthenticatedPrincipal


@dataclass(frozen=True, slots=True)
class CreateSupportQueueCommand:
    principal: AuthenticatedPrincipal
    name: str
    description: str
    is_default: bool
    idempotency_key: str
    site_id: str | None = None


@dataclass(frozen=True, slots=True)
class UpdateSupportQueueCommand:
    principal: AuthenticatedPrincipal
    queue_id: str
    name: str | None
    description: str | None
    status: str | None
    is_default: bool | None
    idempotency_key: str
    site_id: str | None = None


@dataclass(frozen=True, slots=True)
class UpdateSupportQueueMembersCommand:
    principal: AuthenticatedPrincipal
    queue_id: str
    agent_ids: tuple[str, ...]
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class SupportConfigurationResult:
    queues: tuple[SupportQueue, ...]
    agents: tuple[SupportAgentOption, ...]
    canned_replies: tuple[CannedReply, ...]


@dataclass(frozen=True, slots=True)
class CreateCannedReplyCommand:
    principal: AuthenticatedPrincipal
    title: str
    content: str
    shortcut: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ListCustomerMemoryQuery:
    principal: AuthenticatedPrincipal
    customer_id: str


@dataclass(frozen=True, slots=True)
class UpsertCustomerMemoryCommand:
    principal: AuthenticatedPrincipal
    customer_id: str
    kind: MemoryKind
    content: str
    source_type: str
    source_id: str
    confidence: float
    consent_status: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class DeleteCustomerMemoryCommand:
    principal: AuthenticatedPrincipal
    customer_id: str
    memory_id: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class CustomerMemoryResult:
    items: tuple[CustomerMemoryItem, ...]


@dataclass(frozen=True, slots=True)
class ListMemoryCandidatesQuery:
    principal: AuthenticatedPrincipal
    customer_id: str


@dataclass(frozen=True, slots=True)
class ProposeMemoryCandidateCommand:
    principal: AuthenticatedPrincipal
    customer_id: str
    conversation_id: str
    kind: MemoryKind
    normalized_value: dict[str, object]
    display_text: str
    source_message_ids: tuple[str, ...]
    source_trace_id: str
    confidence: float
    sensitivity: str
    consent_required: bool
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ReviewMemoryCandidateCommand:
    principal: AuthenticatedPrincipal
    customer_id: str
    candidate_id: str
    approve: bool
    rejection_reason: str | None
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class MemoryCandidateResult:
    candidates: tuple[MemoryCandidate, ...]
    memory: CustomerMemoryItem | None = None


@dataclass(frozen=True, slots=True)
class ListResolutionEpisodesQuery:
    principal: AuthenticatedPrincipal
    customer_id: str


@dataclass(frozen=True, slots=True)
class DeleteResolutionEpisodeCommand:
    principal: AuthenticatedPrincipal
    customer_id: str
    episode_id: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ResolutionEpisodeResult:
    items: tuple[ResolutionEpisode, ...]


@dataclass(frozen=True, slots=True)
class ConversationResult:
    workspace: ConversationWorkspace
