from app.domain.models.experience import (
    ExperienceGuardrailDecision,
    ExperienceGuidance,
    ExperienceOutcome,
    IssueResolutionStatus,
    OutcomeConflictStatus,
    OutcomeDecision,
    OutcomeEvidenceGrade,
    OutcomeSource,
)

_POSITIVE_OUTCOMES = {
    ExperienceOutcome.SUCCESSFUL_SELF_SERVICE,
    ExperienceOutcome.SUCCESSFUL_HUMAN_RESOLUTION,
    ExperienceOutcome.CORRECT_POLICY_HANDOFF,
}
_NEGATIVE_OUTCOMES = {
    ExperienceOutcome.AVOIDABLE_HANDOFF,
    ExperienceOutcome.FAILED_ANSWER,
    ExperienceOutcome.REOPENED,
}
_SOURCE_PRIORITY = {
    OutcomeSource.UNKNOWN: 0,
    OutcomeSource.BEHAVIOR: 1,
    OutcomeSource.CSAT: 2,
    OutcomeSource.TICKET: 3,
    OutcomeSource.CUSTOMER_CONFIRMATION: 4,
    OutcomeSource.HUMAN_VERIFIED: 5,
    OutcomeSource.REOPEN: 6,
}
_GRADE_PRIORITY = {
    OutcomeEvidenceGrade.UNKNOWN: 0,
    OutcomeEvidenceGrade.WEAK: 1,
    OutcomeEvidenceGrade.MODERATE: 2,
    OutcomeEvidenceGrade.STRONG: 3,
    OutcomeEvidenceGrade.VERIFIED: 4,
}
_CONTINUATION_MARKERS = (
    "and ",
    "also ",
    "what about",
    "how about",
    "that one",
    "this one",
    "另外",
    "还有",
    "那么",
    "这个",
    "这款",
)
_NEW_TOPIC_MARKERS = (
    "another question",
    "different question",
    "separate issue",
    "另外一个问题",
    "另一个问题",
    "换个问题",
)


def merge_outcome_decision(
    current: OutcomeDecision,
    incoming: OutcomeDecision,
) -> OutcomeDecision:
    """Merge delayed outcome evidence without allowing weak signals to rewrite strong labels."""
    if current.learning_outcome is ExperienceOutcome.UNKNOWN:
        return incoming
    if incoming.learning_outcome is ExperienceOutcome.UNKNOWN:
        return OutcomeDecision(
            learning_outcome=current.learning_outcome,
            resolution_status=_merge_resolution_status(
                current.resolution_status, incoming.resolution_status
            ),
            evidence_grade=current.evidence_grade,
            source=current.source,
            conflict_status=current.conflict_status,
        )

    current_polarity = _polarity(current.learning_outcome)
    incoming_polarity = _polarity(incoming.learning_outcome)
    current_rank = (_SOURCE_PRIORITY[current.source], _GRADE_PRIORITY[current.evidence_grade])
    incoming_rank = (_SOURCE_PRIORITY[incoming.source], _GRADE_PRIORITY[incoming.evidence_grade])

    # A reopen invalidates an earlier success. A later explicit human/customer resolution
    # can resolve that reopened issue again because observations are merged in event order.
    if incoming.source is OutcomeSource.REOPEN or (
        current.source is OutcomeSource.REOPEN
        and incoming.source in {OutcomeSource.HUMAN_VERIFIED, OutcomeSource.CUSTOMER_CONFIRMATION}
    ):
        incoming_rank = (99, incoming_rank[1])

    resolution_status = _merge_resolution_status(
        current.resolution_status, incoming.resolution_status
    )
    if current_polarity == incoming_polarity:
        selected = incoming if incoming_rank >= current_rank else current
        return OutcomeDecision(
            learning_outcome=selected.learning_outcome,
            resolution_status=resolution_status,
            evidence_grade=selected.evidence_grade,
            source=selected.source,
            conflict_status=current.conflict_status,
        )

    if incoming_rank == current_rank:
        return OutcomeDecision(
            learning_outcome=ExperienceOutcome.UNKNOWN,
            resolution_status=resolution_status,
            evidence_grade=incoming.evidence_grade,
            source=incoming.source,
            conflict_status=OutcomeConflictStatus.UNRESOLVED,
        )
    selected, rejected = (
        (incoming, current) if incoming_rank > current_rank else (current, incoming)
    )
    return OutcomeDecision(
        learning_outcome=selected.learning_outcome,
        resolution_status=resolution_status,
        evidence_grade=selected.evidence_grade,
        source=selected.source,
        conflict_status=(
            OutcomeConflictStatus.POSITIVE_OVERRIDDEN
            if _polarity(rejected.learning_outcome) == "positive"
            else OutcomeConflictStatus.NEGATIVE_OVERRIDDEN
        ),
    )


def should_continue_issue(
    *,
    previous_intent: str,
    current_intent: str,
    message: str,
    previous_product_references: tuple[str, ...] = (),
    current_product_references: tuple[str, ...] = (),
    idle_seconds: float = 0,
) -> bool:
    """Resolve conservative issue continuity without asking the model to mint identity."""
    normalized = " ".join(message.casefold().split())
    if idle_seconds > 7 * 24 * 60 * 60:
        return False
    if any(marker in normalized for marker in _NEW_TOPIC_MARKERS):
        return False
    if current_intent == previous_intent:
        return True
    overlap = set(previous_product_references) & set(current_product_references)
    if overlap and current_intent == "general":
        return True
    return current_intent == "general" and any(
        normalized.startswith(marker) or f" {marker}" in normalized
        for marker in _CONTINUATION_MARKERS
    )


def evaluate_experience_guardrails(
    *,
    treatment_outcomes: tuple[ExperienceOutcome, ...],
    control_outcomes: tuple[ExperienceOutcome, ...],
    minimum_samples: int,
    maximum_failure_rate_delta: float,
) -> ExperienceGuardrailDecision:
    if minimum_samples < 1:
        raise ValueError("guardrail minimum samples must be positive")
    if not 0 <= maximum_failure_rate_delta <= 1:
        raise ValueError("maximum failure rate delta must be between 0 and 1")
    treatment_failure_rate = _outcome_failure_rate(treatment_outcomes)
    control_failure_rate = _outcome_failure_rate(control_outcomes)
    failure_rate_delta = treatment_failure_rate - control_failure_rate
    enough_samples = (
        len(treatment_outcomes) >= minimum_samples and len(control_outcomes) >= minimum_samples
    )
    pause = enough_samples and failure_rate_delta > maximum_failure_rate_delta
    return ExperienceGuardrailDecision(
        status="paused" if pause else "healthy" if enough_samples else "observing",
        pause_release=pause,
        treatment_samples=len(treatment_outcomes),
        control_samples=len(control_outcomes),
        treatment_failure_rate=treatment_failure_rate,
        control_failure_rate=control_failure_rate,
        failure_rate_delta=failure_rate_delta,
    )


def should_quarantine_experience_memory(
    *,
    outcomes: tuple[ExperienceOutcome, ...],
    minimum_samples: int,
    maximum_negative_transfer_rate: float,
) -> bool:
    if minimum_samples < 1:
        raise ValueError("quarantine minimum samples must be positive")
    if not 0 <= maximum_negative_transfer_rate <= 1:
        raise ValueError("maximum negative transfer rate must be between 0 and 1")
    return len(outcomes) >= minimum_samples and (
        _outcome_failure_rate(outcomes) > maximum_negative_transfer_rate
    )


def select_contrastive_experience(
    candidates: tuple[ExperienceGuidance, ...],
    limit: int,
) -> tuple[ExperienceGuidance, ...]:
    if limit < 1:
        return ()
    selected: list[ExperienceGuidance] = []
    for polarity in ("negative", "positive"):
        item = next(
            (candidate for candidate in candidates if candidate.polarity.value == polarity),
            None,
        )
        if item is not None:
            selected.append(item)
    for candidate in candidates:
        if len(selected) >= limit:
            break
        if candidate not in selected:
            selected.append(candidate)
    return tuple(selected[:limit])


def _polarity(outcome: ExperienceOutcome) -> str:
    if outcome in _POSITIVE_OUTCOMES:
        return "positive"
    if outcome in _NEGATIVE_OUTCOMES:
        return "negative"
    return "neutral"


def _outcome_failure_rate(outcomes: tuple[ExperienceOutcome, ...]) -> float:
    if not outcomes:
        return 0.0
    return sum(outcome in _NEGATIVE_OUTCOMES for outcome in outcomes) / len(outcomes)


def _merge_resolution_status(
    current: IssueResolutionStatus,
    incoming: IssueResolutionStatus,
) -> IssueResolutionStatus:
    if incoming is IssueResolutionStatus.OBSERVING and current in {
        IssueResolutionStatus.RESOLVED,
        IssueResolutionStatus.FAILED,
        IssueResolutionStatus.REOPENED,
        IssueResolutionStatus.CENSORED,
    }:
        return current
    return incoming
