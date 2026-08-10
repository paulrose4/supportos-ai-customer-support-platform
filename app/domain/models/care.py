from dataclasses import dataclass
from enum import StrEnum


class CareRiskTier(StrEnum):
    LOW = "low"
    MATERIAL_DEPENDENT = "material_dependent"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class ApprovedCareStep:
    step_id: str
    instruction: str


@dataclass(frozen=True, slots=True)
class ApprovedCareProcedure:
    procedure_id: str
    material: str
    steps: tuple[ApprovedCareStep, ...]
    prohibited_actions: tuple[str, ...]
    source: str
    chunk_id: str
    version: str


@dataclass(frozen=True, slots=True)
class CareGuidanceDecision:
    status: str
    risk_tier: CareRiskTier | None = None
    material: str | None = None
    procedure: ApprovedCareProcedure | None = None
    product_source: str | None = None
    reason_code: str | None = None
