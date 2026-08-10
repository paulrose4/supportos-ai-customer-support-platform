from datetime import datetime

from pydantic import BaseModel, Field


class SiteItem(BaseModel):
    site_id: str
    name: str
    base_url: str
    status: str


class SiteListResponse(BaseModel):
    items: list[SiteItem] = Field(default_factory=list)


class InboxItem(BaseModel):
    conversation_id: str
    site_id: str | None
    customer_id: str | None
    customer_display_name: str | None
    visitor_ip_address: str | None
    visitor_country_code: str | None
    channel: str
    status: str
    ownership_mode: str
    assigned_agent_id: str | None
    queue_id: str | None
    priority: str
    tags: list[str] = Field(default_factory=list)
    risk_level: int
    unread_count: int
    identity_verified: bool
    last_message_preview: str | None
    last_message_at: datetime | None
    first_response_at: datetime | None
    first_human_response_at: datetime | None
    resolved_at: datetime | None
    last_read_at: datetime | None
    updated_at: datetime
    handoff_reason: str | None = None
    sla_due_at: datetime | None = None
    routing_version: int = 0


class InboxResponse(BaseModel):
    items: list[InboxItem] = Field(default_factory=list)
    next_cursor: str | None = None
    total: int | None = None


class InboxCountsResponse(BaseModel):
    all: int
    mine: int
    waiting_human: int
    sla_risk: int
    unread: int
    priority_risk: int
    high_intent: int | None = Field(default=None, deprecated=True)
    resolved: int


class CustomerDirectoryItemResponse(BaseModel):
    customer_id: str
    display_name: str
    conversation_count: int
    last_conversation_at: datetime | None


class CustomerDirectoryResponse(BaseModel):
    items: list[CustomerDirectoryItemResponse] = Field(default_factory=list)
    next_cursor: str | None = None
    total: int | None = None


class ConversationMessage(BaseModel):
    message_id: str
    role: str
    content: str
    message_type: str
    author_subject_id: str | None
    metadata: dict = Field(default_factory=dict)
    created_at: datetime | None


class ConversationWorkspaceResponse(BaseModel):
    conversation: InboxItem
    messages: list[ConversationMessage] = Field(default_factory=list)
    handoff_context: dict | None = None


class IdempotentActionRequest(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=200)


class AgentMessageRequest(IdempotentActionRequest):
    content: str = Field(min_length=1, max_length=5000)


class InternalNoteRequest(IdempotentActionRequest):
    content: str = Field(min_length=1, max_length=5000)


class ConversationRoutingRequest(IdempotentActionRequest):
    assigned_agent_id: str | None = Field(default=None, max_length=100)
    queue_id: str | None = Field(default=None, max_length=100)
    priority: str = Field(default="normal", pattern="^(low|normal|high|urgent)$")
    tags: list[str] = Field(default_factory=list, max_length=20)


class ManualHandoffRequest(IdempotentActionRequest):
    summary: str = Field(min_length=1, max_length=2000)
    queue_id: str | None = Field(default=None, max_length=100)
    priority: str = Field(default="normal", pattern="^(low|normal|high|urgent)$")


class SupportQueueResponse(BaseModel):
    queue_id: str
    name: str
    description: str
    is_default: bool
    status: str = "active"
    site_id: str | None = None


class SupportQueueMemberResponse(BaseModel):
    agent_id: str
    display_name: str
    role: str
    status: str


class SupportQueueMembersResponse(BaseModel):
    items: list[SupportQueueMemberResponse] = Field(default_factory=list)


class UpdateSupportQueueMembersRequest(IdempotentActionRequest):
    agent_ids: list[str] = Field(default_factory=list, max_length=200)


class CreateSupportQueueRequest(IdempotentActionRequest):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    is_default: bool = False
    site_id: str | None = Field(default=None, max_length=100)


class UpdateSupportQueueRequest(IdempotentActionRequest):
    name: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    status: str | None = Field(default=None, pattern="^(active|disabled)$")
    is_default: bool | None = None
    site_id: str | None = Field(default=None, max_length=100)


class SupportAgentOptionResponse(BaseModel):
    agent_id: str
    display_name: str


class CannedReplyResponse(BaseModel):
    reply_id: str
    title: str
    content: str
    shortcut: str


class SupportConfigurationResponse(BaseModel):
    queues: list[SupportQueueResponse] = Field(default_factory=list)
    agents: list[SupportAgentOptionResponse] = Field(default_factory=list)
    canned_replies: list[CannedReplyResponse] = Field(default_factory=list)


class CreateCannedReplyRequest(IdempotentActionRequest):
    title: str = Field(min_length=1, max_length=160)
    content: str = Field(min_length=1, max_length=5000)
    shortcut: str = Field(min_length=1, max_length=80)


class CustomerMemoryItemResponse(BaseModel):
    memory_id: str
    customer_id: str
    kind: str
    content: str
    source_type: str
    source_id: str
    confidence: float
    consent_status: str
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime
    status: str
    normalized_value: dict[str, object] = Field(default_factory=dict)
    sensitivity: str
    last_verified_at: datetime | None
    last_used_at: datetime | None
    use_count: int
    superseded_by: str | None
    legacy: bool


class CustomerMemoryListResponse(BaseModel):
    items: list[CustomerMemoryItemResponse] = Field(default_factory=list)


class UpsertCustomerMemoryRequest(IdempotentActionRequest):
    kind: str
    content: str = Field(min_length=1, max_length=1000)
    source_type: str
    source_id: str = Field(min_length=1, max_length=100)
    confidence: float = Field(ge=0.0, le=1.0)
    consent_status: str


class ProposeMemoryCandidateRequest(IdempotentActionRequest):
    conversation_id: str = Field(min_length=1, max_length=100)
    kind: str
    normalized_value: dict[str, str]
    display_text: str = Field(min_length=1, max_length=1000)
    source_message_ids: list[str] = Field(default_factory=list, max_length=20)
    source_trace_id: str = Field(min_length=1, max_length=100)
    confidence: float = Field(ge=0.8, le=1.0)
    sensitivity: str = "standard"
    consent_required: bool = True


class ReviewMemoryCandidateRequest(IdempotentActionRequest):
    approve: bool
    rejection_reason: str | None = Field(default=None, max_length=500)


class MemoryCandidateResponse(BaseModel):
    candidate_id: str
    customer_id: str
    conversation_id: str
    kind: str
    normalized_value: dict[str, object]
    display_text: str
    source_message_ids: list[str]
    source_trace_id: str
    confidence: float
    sensitivity: str
    consent_required: bool
    status: str
    rejection_reason: str | None
    reviewed_by: str | None
    reviewed_at: datetime | None
    created_at: datetime
    expires_at: datetime


class MemoryCandidateListResponse(BaseModel):
    items: list[MemoryCandidateResponse] = Field(default_factory=list)
    memory: CustomerMemoryItemResponse | None = None


class ResolutionEpisodeResponse(BaseModel):
    episode_id: str
    customer_id: str
    conversation_id: str
    intent: str
    issue_summary: str
    product_reference: str | None
    order_reference: str | None
    actions_taken: list[str]
    resolution_summary: str
    resolution_source: str
    resolved_at: datetime
    reopened_at: datetime | None
    confidence: float
    expires_at: datetime
    status: str


class ResolutionEpisodeListResponse(BaseModel):
    items: list[ResolutionEpisodeResponse] = Field(default_factory=list)
