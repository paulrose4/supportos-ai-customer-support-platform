from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class ConversationStatus(StrEnum):
    OPEN = "open"
    WAITING_HUMAN = "waiting_human"
    RESOLVED = "resolved"


class OwnershipMode(StrEnum):
    AI = "ai"
    QUEUED = "queued"
    HUMAN = "human"


class ConversationPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class MemoryKind(StrEnum):
    PREFERENCE = "preference"
    VERIFIED_PRODUCT = "verified_product"
    TROUBLESHOOTING = "troubleshooting"
    RESOLUTION = "resolution"


class MemoryCandidateStatus(StrEnum):
    PROPOSED = "proposed"
    AWAITING_CONSENT = "awaiting_consent"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


@dataclass(frozen=True, slots=True)
class SupportSite:
    site_id: str
    tenant_id: str
    name: str
    base_url: str
    status: str
    created_at: datetime
    updated_at: datetime
    primary_language: str = "en"
    verification_status: str = "verified"
    duplicate_product_policy: str | None = None
    duplicate_product_order: str | None = None


@dataclass(frozen=True, slots=True)
class InboxConversation:
    conversation_id: str
    tenant_id: str
    site_id: str | None
    customer_id: str | None
    customer_display_name: str | None
    channel: str
    status: ConversationStatus
    ownership_mode: OwnershipMode
    assigned_agent_id: str | None
    risk_level: int
    unread_count: int
    identity_verified: bool
    last_message_preview: str | None
    last_message_at: datetime | None
    updated_at: datetime
    queue_id: str | None = None
    priority: ConversationPriority = ConversationPriority.NORMAL
    tags: tuple[str, ...] = ()
    first_response_at: datetime | None = None
    first_human_response_at: datetime | None = None
    resolved_at: datetime | None = None
    last_read_at: datetime | None = None
    handoff_reason: str | None = None
    sla_due_at: datetime | None = None
    visitor_ip_address: str | None = None
    visitor_country_code: str | None = None
    routing_version: int = 0


@dataclass(frozen=True, slots=True)
class CustomerDirectoryItem:
    customer_id: str
    tenant_id: str
    display_name: str
    conversation_count: int
    last_conversation_at: datetime | None


@dataclass(frozen=True, slots=True)
class InboxCounts:
    all: int
    mine: int
    waiting_human: int
    sla_risk: int
    unread: int
    priority_risk: int

    @property
    def high_intent(self) -> int:
        """Compatibility alias; this count is risk/priority, not purchase intent."""

        return self.priority_risk

    resolved: int


@dataclass(frozen=True, slots=True)
class ConversationRoutingState:
    conversation_id: str
    status: ConversationStatus
    ownership_mode: OwnershipMode
    assigned_agent_id: str | None = None
    handoff_id: str | None = None

    @property
    def requires_human(self) -> bool:
        return self.status is not ConversationStatus.RESOLVED and self.ownership_mode in {
            OwnershipMode.QUEUED,
            OwnershipMode.HUMAN,
        }


@dataclass(frozen=True, slots=True)
class HumanSupportMessage:
    message_id: str
    conversation_id: str
    content: str
    created_at: datetime
    cursor: int = 0


@dataclass(frozen=True, slots=True)
class SupportMessage:
    message_id: str
    conversation_id: str
    role: str
    content: str
    message_type: str
    author_subject_id: str | None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ConversationWorkspace:
    conversation: InboxConversation
    messages: tuple[SupportMessage, ...]
    handoff_context: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class SupportQueue:
    queue_id: str
    tenant_id: str
    name: str
    description: str
    is_default: bool
    status: str
    site_id: str | None = None


@dataclass(frozen=True, slots=True)
class SupportAgentOption:
    agent_id: str
    display_name: str


@dataclass(frozen=True, slots=True)
class SupportQueueMember:
    agent_id: str
    display_name: str
    role: str
    status: str


@dataclass(frozen=True, slots=True)
class CannedReply:
    reply_id: str
    tenant_id: str
    title: str
    content: str
    shortcut: str
    status: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class CustomerMemoryItem:
    memory_id: str
    tenant_id: str
    customer_id: str
    kind: MemoryKind
    content: str
    source_type: str
    source_id: str
    confidence: float
    consent_status: str
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime
    status: str = "active"
    normalized_value: dict[str, Any] = field(default_factory=dict)
    sensitivity: str = "standard"
    last_verified_at: datetime | None = None
    last_used_at: datetime | None = None
    use_count: int = 0
    superseded_by: str | None = None
    legacy: bool = False


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    candidate_id: str
    tenant_id: str
    customer_id: str
    conversation_id: str
    kind: MemoryKind
    normalized_value: dict[str, Any]
    display_text: str
    source_message_ids: tuple[str, ...]
    source_trace_id: str
    confidence: float
    sensitivity: str
    consent_required: bool
    status: MemoryCandidateStatus
    rejection_reason: str | None
    reviewed_by: str | None
    reviewed_at: datetime | None
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ResolutionEpisode:
    episode_id: str
    tenant_id: str
    customer_id: str
    conversation_id: str
    intent: str
    issue_summary: str
    product_reference: str | None
    order_reference: str | None
    actions_taken: tuple[str, ...]
    resolution_summary: str
    resolution_source: str
    resolved_at: datetime
    reopened_at: datetime | None
    confidence: float
    expires_at: datetime
    status: str
