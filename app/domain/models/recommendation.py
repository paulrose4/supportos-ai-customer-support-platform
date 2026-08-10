from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from app.domain.models.product_catalog import ProductSnapshot


class ProductRecommendationMode(StrEnum):
    EXACT_MATCH = "exact_match"
    SUBSTITUTE = "substitute"
    PREFERENCE = "preference"


class RecommendationConstraintStrength(StrEnum):
    HARD = "hard"
    SOFT = "soft"
    VERIFICATION_REQUIRED = "verification_required"


@dataclass(frozen=True, slots=True)
class ProductRecommendationConstraint:
    field: str
    operator: str
    value: str
    strength: RecommendationConstraintStrength
    source: str = "current_message"
    weight: float = 1.0


@dataclass(frozen=True, slots=True)
class ProductRecommendationPlan:
    mode: ProductRecommendationMode
    query_text: str
    constraints: tuple[ProductRecommendationConstraint, ...] = ()
    destination: str | None = None
    source_product_key: str | None = None

    @property
    def hard_constraints(self) -> tuple[ProductRecommendationConstraint, ...]:
        return tuple(
            item
            for item in self.constraints
            if item.strength is RecommendationConstraintStrength.HARD
        )

    @property
    def soft_constraints(self) -> tuple[ProductRecommendationConstraint, ...]:
        return tuple(
            item
            for item in self.constraints
            if item.strength is RecommendationConstraintStrength.SOFT
        )


@dataclass(frozen=True, slots=True)
class ProductRecommendationScore:
    dense: float = 0.0
    sparse: float = 0.0
    structured: float = 0.0
    rerank: float = 0.0
    preference: float = 0.0
    price_fit: float = 0.0
    business: float = 0.0
    evidence_coverage: float = 0.0
    review: float = 0.0
    final: float = 0.0


@dataclass(frozen=True, slots=True)
class RecommendationSoftPreference:
    field: str
    value: str
    weight: float


@dataclass(frozen=True, slots=True)
class RecommendationPlannerAdvice:
    soft_preferences: tuple[RecommendationSoftPreference, ...] = ()
    request_second_soft_retrieval: bool = False
    clarify_constraint: str | None = None


@dataclass(frozen=True, slots=True)
class CandidateReviewInput:
    candidate_key: str
    facts: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class CandidateReview:
    candidate_key: str
    relevant: bool
    score: float
    reason: str
    evidence_field_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProductRecommendationCandidate:
    product: ProductSnapshot
    score: ProductRecommendationScore
    match_reasons: tuple[str, ...] = ()
    tradeoffs: tuple[str, ...] = ()
    missing_facts: tuple[str, ...] = ()
    evidence_fields: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProductRecommendationResult:
    mode: ProductRecommendationMode
    candidates: tuple[ProductRecommendationCandidate, ...] = ()
    hard_constraint_labels: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    no_match_reason: str | None = None
