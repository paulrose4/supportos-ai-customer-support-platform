from dataclasses import dataclass
from datetime import datetime

from app.domain.models import AuthenticatedPrincipal, ExperienceGuidance


@dataclass(frozen=True, slots=True)
class RetrieveTenantExperienceCommand:
    principal: AuthenticatedPrincipal
    conversation_id: str
    trace_id: str
    message: str
    intent: str | None
    product_references: tuple[str, ...]
    risk_level: int


@dataclass(frozen=True, slots=True)
class TenantExperienceResult:
    guidance: tuple[ExperienceGuidance, ...]
    retrieved_ids: tuple[str, ...]
    treatment: str
    release_version: str
    applied: bool
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExperienceLearningCycleResult:
    tenant_id: str
    release_version: str
    reconciled_outcomes: int
    guardrail_report: dict[str, object]
    case_memories_built: int
    patterns_consolidated: int
    improvement_candidates_created: int
    gate_report: dict[str, object]
    completed_at: datetime
