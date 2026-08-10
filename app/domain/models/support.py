from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class HandoffStatus(StrEnum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"


class BusinessResourceType(StrEnum):
    ORDER = "order"
    SUPPORT_TICKET = "support_ticket"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class BusinessQueryIntent:
    resource_type: BusinessResourceType
    resource_id: str | None


@dataclass(frozen=True, slots=True)
class Order:
    order_id: str
    tenant_id: str
    customer_id: str
    status: str
    version: int
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SupportTicket:
    ticket_id: str
    tenant_id: str
    customer_id: str | None
    conversation_id: str | None
    status: str
    subject: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ToolExecution:
    execution_id: str
    tool_name: str
    status: str
    input_summary: dict[str, Any] = field(default_factory=dict)
    output_summary: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class HandoffNotification:
    tenant_id: str
    site_id: str
    handoff_id: str
    conversation_id: str
    reason_code: str
    risk_level: int
    summary: str


@dataclass(frozen=True, slots=True)
class HandoffRequest:
    handoff_id: str
    tenant_id: str
    customer_id: str | None
    conversation_id: str
    reason_code: str
    risk_level: int
    summary: str
    verified_evidence: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    failed_tools: tuple[str, ...] = field(default_factory=tuple)
    knowledge_sources: tuple[str, ...] = field(default_factory=tuple)
    created_at: datetime | None = None
    status: HandoffStatus = HandoffStatus.PENDING
    idempotency_key: str = ""
    trace_id: str = ""
    sla_policy_version: str | None = None
    expected_response_at: datetime | None = None
    priority: str = "normal"
    assigned_agent_id: str | None = None
    accepted_at: datetime | None = None
    resolved_at: datetime | None = None
    user_intent: str | None = None
    unresolved_question: str | None = None
    ai_attempt: str | None = None
    suggested_next_action: str | None = None
    reply_draft: str | None = None
    queue_id: str | None = None
    context_schema_version: int = 2
    customer_language: str | None = None
    customer_region: str | None = None
    identity_status: str | None = None
    product_ids: tuple[str, ...] = ()
    order_id: str | None = None
    confirmed_fields: tuple[str, ...] = ()
    customer_sentiment: str | None = None
    customer_request: str | None = None
    commitment_deadline: datetime | None = None
