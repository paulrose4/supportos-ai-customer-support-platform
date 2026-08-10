from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class ExperienceOutcome(StrEnum):
    OPEN = "open"
    AWAITING_HUMAN_OUTCOME = "awaiting_human_outcome"
    OBSERVING = "observing"
    SUCCESSFUL_SELF_SERVICE = "successful_self_service"
    SUCCESSFUL_HUMAN_RESOLUTION = "successful_human_resolution"
    CORRECT_POLICY_HANDOFF = "correct_policy_handoff"
    AVOIDABLE_HANDOFF = "avoidable_handoff"
    FAILED_ANSWER = "failed_answer"
    REOPENED = "reopened"
    CENSORED = "censored"
    UNKNOWN = "unknown"


class ExperienceMemoryStatus(StrEnum):
    SHADOW = "shadow"
    OPERATIONAL_ACTIVE = "operational_active"
    APPROVED_FACTUAL = "approved_factual"
    STALE = "stale"
    INACTIVE = "inactive"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ExperiencePolarity(StrEnum):
    NEGATIVE = "negative"
    POSITIVE = "positive"
    NEUTRAL = "neutral"


class OutcomeEvidenceGrade(StrEnum):
    UNKNOWN = "unknown"
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"
    VERIFIED = "verified"


class IssueResolutionStatus(StrEnum):
    OPEN = "open"
    OBSERVING = "observing"
    RESOLVED = "resolved"
    FAILED = "failed"
    REOPENED = "reopened"
    CENSORED = "censored"


class OutcomeConflictStatus(StrEnum):
    NONE = "none"
    POSITIVE_OVERRIDDEN = "positive_overridden"
    NEGATIVE_OVERRIDDEN = "negative_overridden"
    UNRESOLVED = "unresolved"


class OutcomeSource(StrEnum):
    UNKNOWN = "unknown"
    BEHAVIOR = "behavior"
    CSAT = "csat"
    TICKET = "ticket"
    CUSTOMER_CONFIRMATION = "customer_confirmation"
    HUMAN_VERIFIED = "human_verified"
    REOPEN = "reopen"


@dataclass(frozen=True, slots=True)
class OutcomeDecision:
    learning_outcome: ExperienceOutcome
    resolution_status: IssueResolutionStatus
    evidence_grade: OutcomeEvidenceGrade
    source: OutcomeSource
    conflict_status: OutcomeConflictStatus = OutcomeConflictStatus.NONE


@dataclass(frozen=True, slots=True)
class ExperienceDependencyFingerprint:
    knowledge_version_ids: tuple[str, ...] = ()
    policy_version: str | None = None
    prompt_version: str | None = None
    rule_set_version: str | None = None
    model_version: str | None = None
    product_snapshot_id: str | None = None
    summary_schema_version: int = 1


@dataclass(frozen=True, slots=True)
class ExperienceApplicability:
    intents: tuple[str, ...] = ()
    product_references: tuple[str, ...] = ()
    materials: tuple[str, ...] = ()
    regions: tuple[str, ...] = ()
    minimum_risk: int = 0
    maximum_risk: int = 1
    exclusions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExperienceGuidance:
    memory_id: str
    polarity: ExperiencePolarity
    summary: str
    avoid_actions: tuple[str, ...] = ()
    suggested_clarifications: tuple[str, ...] = ()
    retrieval_hints: tuple[str, ...] = ()
    escalation_hint: str | None = None
    approved_evidence_refs: tuple[str, ...] = ()
    applicability: ExperienceApplicability = field(default_factory=ExperienceApplicability)
    outcome_evidence_grade: OutcomeEvidenceGrade = OutcomeEvidenceGrade.UNKNOWN
    freshness: str = "current"
    sample_count: int = 0
    uncertainty: float = 1.0
    wilson_lower_bound: float = 0.0
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class IssueOutcomeEpisode:
    episode_id: str
    issue_id: str
    tenant_id: str
    site_id: str
    conversation_id: str
    intent: str
    status: ExperienceOutcome
    started_at: datetime
    updated_at: datetime
    outcome_observed_at: datetime | None = None
    handoff_id: str | None = None
    trigger_trace_id: str | None = None
    source_message_ids: tuple[str, ...] = ()
    issue_summary: str = ""
    ai_attempt_summary: str = ""
    human_resolution_summary: str = ""
    handoff_reason: str | None = None
    correction_labels: tuple[str, ...] = ()
    product_references: tuple[str, ...] = ()
    evidence_references: tuple[str, ...] = ()
    dependencies: ExperienceDependencyFingerprint = field(
        default_factory=ExperienceDependencyFingerprint
    )
    outcome_evidence_grade: OutcomeEvidenceGrade = OutcomeEvidenceGrade.UNKNOWN
    resolution_status: IssueResolutionStatus = IssueResolutionStatus.OPEN
    learning_outcome: ExperienceOutcome = ExperienceOutcome.UNKNOWN
    outcome_source: OutcomeSource = OutcomeSource.UNKNOWN
    outcome_conflict_status: OutcomeConflictStatus = OutcomeConflictStatus.NONE
    actor_cohort_hash: str = ""
    revision: int = 1


@dataclass(frozen=True, slots=True)
class TenantCaseMemory:
    memory_id: str
    tenant_id: str
    site_id: str
    source_episode_id: str
    issue_signature: str
    guidance: ExperienceGuidance
    status: ExperienceMemoryStatus
    dependencies: ExperienceDependencyFingerprint
    created_at: datetime
    updated_at: datetime
    source_actor_cohort_hash: str = ""
    expires_at: datetime | None = None
    superseded_by: str | None = None
    use_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    negative_transfer_count: int = 0


@dataclass(frozen=True, slots=True)
class TenantPatternMemory:
    pattern_id: str
    tenant_id: str
    site_id: str | None
    fingerprint: str
    guidance: ExperienceGuidance
    source_memory_ids: tuple[str, ...]
    status: ExperienceMemoryStatus
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ExperienceMemoryUsage:
    usage_id: str
    tenant_id: str
    conversation_id: str
    trace_id: str
    memory_id: str
    release_version: str
    treatment: str
    retrieved: bool
    used_for: tuple[str, ...]
    occurred_at: datetime
    issue_id: str | None = None
    eligible: bool = True
    selected: bool = True
    influenced: bool = False
    outcome_attributed: bool = False


@dataclass(frozen=True, slots=True)
class ExperienceReleasePolicy:
    release_version: str
    mode: str
    rollout_percent: int
    status: str
    included_memory_ids: tuple[str, ...] = ()
    excluded_memory_ids: tuple[str, ...] = ()
    eligible_intents: tuple[str, ...] = ()
    eligible_sites: tuple[str, ...] = ()
    risk_ceiling: int = 1
    dataset_version: str | None = None


@dataclass(frozen=True, slots=True)
class ExperienceGuardrailDecision:
    status: str
    pause_release: bool
    treatment_samples: int
    control_samples: int
    treatment_failure_rate: float
    control_failure_rate: float
    failure_rate_delta: float
