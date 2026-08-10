from dataclasses import dataclass, field
from enum import IntEnum, StrEnum

from app.domain.models.answer import RecommendedProduct
from app.domain.models.memory import ConversationWorkingMemory
from app.domain.models.support import ToolExecution


class RiskLevel(IntEnum):
    PUBLIC = 0
    AUTHENTICATED_READ = 1
    HIGH_IMPACT_REQUEST = 2
    SEVERE = 3


class ResponseKind(StrEnum):
    ANSWER = "answer"
    CLARIFICATION = "clarification"
    HANDOFF = "handoff"


@dataclass(frozen=True, slots=True)
class ConversationTurn:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class AgentResponse:
    conversation_id: str
    message: str
    kind: ResponseKind
    risk_level: RiskLevel
    trace_id: str
    handoff_id: str | None = None
    handoff_reason: str | None = None
    citations: tuple[str, ...] = ()
    related_links: tuple[str, ...] = ()
    recommended_products: tuple[RecommendedProduct, ...] = ()
    tool_executions: tuple[ToolExecution, ...] = ()
    care_procedure_ids: tuple[str, ...] = ()
    care_step_ids: tuple[str, ...] = ()
    memory_state: ConversationWorkingMemory | None = None
    used_memory_ids: tuple[str, ...] = ()
    used_experience_ids: tuple[str, ...] = ()
    sales_plan: dict[str, object] = field(default_factory=dict)
    response_brief: dict[str, object] = field(default_factory=dict)
    response_quality: dict[str, object] = field(default_factory=dict)
    turn_plan: dict[str, object] = field(default_factory=dict)
    validation_reason: str | None = None
    knowledge_status: str | None = None
    retrieval_degraded: bool = False
    validation_rewrite_count: int = 0
    model_version: str | None = None
    planner_model_version: str | None = None
    evidence_ids: tuple[str, ...] = ()
