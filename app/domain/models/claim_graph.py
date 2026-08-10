from dataclasses import dataclass
from enum import StrEnum


class ClaimStatus(StrEnum):
    SUPPORTED = "supported"
    SUPPORTED_WITH_QUALIFICATION = "supported_with_qualification"
    CONFLICTING = "conflicting"
    MISSING = "missing"
    PROHIBITED = "prohibited"


@dataclass(frozen=True, slots=True)
class ResolvedFact:
    fact_id: str
    subject: str
    predicate: str
    value: str
    source: str
    citation: str | None = None
    freshness: str = "current"
    scope: str = "knowledge"


@dataclass(frozen=True, slots=True)
class ResponseClaim:
    claim_id: str
    text: str
    status: ClaimStatus
    fact_ids: tuple[str, ...] = ()
    sub_question_ids: tuple[str, ...] = ()
    qualification: str | None = None


@dataclass(frozen=True, slots=True)
class ClaimGraph:
    facts: tuple[ResolvedFact, ...] = ()
    claims: tuple[ResponseClaim, ...] = ()
    unresolved_sub_question_ids: tuple[str, ...] = ()
    policy_version: str = "claim-graph-v1"


@dataclass(frozen=True, slots=True)
class SemanticCoverageReview:
    passed: bool
    answered_sub_question_ids: tuple[str, ...] = ()
    missing_sub_question_ids: tuple[str, ...] = ()
    unsupported_claims: tuple[str, ...] = ()
    repair_instructions: tuple[str, ...] = ()
    policy_version: str = "semantic-response-review-v1"
