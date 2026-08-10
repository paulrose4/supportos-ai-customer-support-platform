from datetime import datetime

from pydantic import BaseModel, Field


class HandoffQueueItem(BaseModel):
    handoff_id: str
    conversation_id: str
    customer_id: str | None
    reason_code: str
    risk_level: int
    summary: str
    status: str
    priority: str
    queue_id: str | None
    assigned_agent_id: str | None
    accepted_at: datetime | None
    resolved_at: datetime | None
    failed_tools: list[str] = Field(default_factory=list)
    knowledge_sources: list[str] = Field(default_factory=list)
    user_intent: str | None = None
    unresolved_question: str | None = None
    ai_attempt: str | None = None
    suggested_next_action: str | None = None
    reply_draft: str | None = None
    created_at: datetime | None
    context_schema_version: int
    customer_language: str | None = None
    customer_region: str | None = None
    identity_status: str | None = None
    product_ids: list[str] = Field(default_factory=list)
    order_id: str | None = None
    confirmed_fields: list[str] = Field(default_factory=list)
    customer_sentiment: str | None = None
    customer_request: str | None = None
    commitment_deadline: datetime | None = None


class HandoffQueueResponse(BaseModel):
    items: list[HandoffQueueItem] = Field(default_factory=list)
    next_cursor: str | None = None
