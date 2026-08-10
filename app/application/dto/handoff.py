from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.domain.models import AuthenticatedPrincipal, HandoffRequest, RiskLevel


@dataclass(frozen=True, slots=True)
class CreateHandoffCommand:
    principal: AuthenticatedPrincipal
    conversation_id: str
    reason_code: str
    risk_level: RiskLevel
    summary: str
    trace_id: str
    verified_evidence: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    failed_tools: tuple[str, ...] = field(default_factory=tuple)
    knowledge_sources: tuple[str, ...] = field(default_factory=tuple)
    user_intent: str | None = None
    unresolved_question: str | None = None
    ai_attempt: str | None = None
    suggested_next_action: str | None = None
    reply_draft: str | None = None
    context_schema_version: int = 2
    customer_language: str | None = None
    customer_region: str | None = None
    identity_status: str | None = None
    product_ids: tuple[str, ...] = field(default_factory=tuple)
    order_id: str | None = None
    confirmed_fields: tuple[str, ...] = field(default_factory=tuple)
    customer_sentiment: str | None = None
    customer_request: str | None = None
    queue_id: str | None = None
    priority: str = "normal"
    sla_policy_version: str | None = None
    expected_response_at: datetime | None = None
    commitment_deadline: datetime | None = None


@dataclass(frozen=True, slots=True)
class ListHandoffsQuery:
    principal: AuthenticatedPrincipal
    status: str | None = "pending"
    limit: int = 50
    cursor: str | None = None
    site_id: str | None = None
    assigned_agent_id: str | None = None


@dataclass(frozen=True, slots=True)
class ListHandoffsResult:
    handoffs: tuple[HandoffRequest, ...]
    next_cursor: str | None = None
