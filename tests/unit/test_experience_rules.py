from app.domain.models import (
    ExperienceApplicability,
    ExperienceGuidance,
    ExperienceOutcome,
    ExperiencePolarity,
    IssueResolutionStatus,
    OutcomeConflictStatus,
    OutcomeDecision,
    OutcomeEvidenceGrade,
    OutcomeSource,
)
from app.domain.rules import (
    evaluate_experience_guardrails,
    merge_outcome_decision,
    select_contrastive_experience,
    should_continue_issue,
    should_quarantine_experience_memory,
)


def _decision(
    outcome: ExperienceOutcome,
    source: OutcomeSource,
    *,
    resolution: IssueResolutionStatus = IssueResolutionStatus.RESOLVED,
    grade: OutcomeEvidenceGrade = OutcomeEvidenceGrade.STRONG,
) -> OutcomeDecision:
    return OutcomeDecision(outcome, resolution, grade, source)


def test_csat_cannot_rewrite_verified_human_learning_outcome() -> None:
    human = _decision(
        ExperienceOutcome.AVOIDABLE_HANDOFF,
        OutcomeSource.HUMAN_VERIFIED,
        grade=OutcomeEvidenceGrade.MODERATE,
    )
    positive_csat = _decision(
        ExperienceOutcome.SUCCESSFUL_HUMAN_RESOLUTION,
        OutcomeSource.CSAT,
    )

    result = merge_outcome_decision(human, positive_csat)

    assert result.learning_outcome is ExperienceOutcome.AVOIDABLE_HANDOFF
    assert result.resolution_status is IssueResolutionStatus.RESOLVED
    assert result.conflict_status is OutcomeConflictStatus.POSITIVE_OVERRIDDEN


def test_reopen_invalidates_an_earlier_success() -> None:
    confirmed = _decision(
        ExperienceOutcome.SUCCESSFUL_SELF_SERVICE,
        OutcomeSource.CUSTOMER_CONFIRMATION,
    )
    reopened = _decision(
        ExperienceOutcome.REOPENED,
        OutcomeSource.REOPEN,
        resolution=IssueResolutionStatus.REOPENED,
    )

    result = merge_outcome_decision(confirmed, reopened)

    assert result.learning_outcome is ExperienceOutcome.REOPENED
    assert result.resolution_status is IssueResolutionStatus.REOPENED
    assert result.conflict_status is OutcomeConflictStatus.POSITIVE_OVERRIDDEN


def test_issue_continuity_accepts_reference_followup_but_rejects_explicit_new_topic() -> None:
    assert should_continue_issue(
        previous_intent="product_specification",
        current_intent="general",
        message="And what about this one?",
        previous_product_references=("SKU-1",),
        current_product_references=("SKU-1",),
    )
    assert not should_continue_issue(
        previous_intent="product_specification",
        current_intent="general",
        message="Another question: how long does delivery take?",
    )


def test_issue_continuity_expires_after_long_idle_window() -> None:
    assert not should_continue_issue(
        previous_intent="product_care",
        current_intent="product_care",
        message="How do I clean it?",
        idle_seconds=8 * 24 * 60 * 60,
    )


def test_guardrail_pauses_only_after_sufficient_controlled_evidence() -> None:
    treatment = (ExperienceOutcome.FAILED_ANSWER,) * 8 + (
        ExperienceOutcome.SUCCESSFUL_SELF_SERVICE,
    ) * 2
    control = (ExperienceOutcome.FAILED_ANSWER,) * 2 + (
        ExperienceOutcome.SUCCESSFUL_SELF_SERVICE,
    ) * 8

    observing = evaluate_experience_guardrails(
        treatment_outcomes=treatment,
        control_outcomes=control,
        minimum_samples=20,
        maximum_failure_rate_delta=0.05,
    )
    paused = evaluate_experience_guardrails(
        treatment_outcomes=treatment,
        control_outcomes=control,
        minimum_samples=10,
        maximum_failure_rate_delta=0.05,
    )

    assert observing.status == "observing"
    assert not observing.pause_release
    assert paused.status == "paused"
    assert paused.pause_release


def test_memory_quarantine_requires_sample_floor_and_bad_outcomes() -> None:
    failures = (ExperienceOutcome.REOPENED,) * 6
    assert not should_quarantine_experience_memory(
        outcomes=failures,
        minimum_samples=10,
        maximum_negative_transfer_rate=0.25,
    )
    assert should_quarantine_experience_memory(
        outcomes=failures * 2,
        minimum_samples=10,
        maximum_negative_transfer_rate=0.25,
    )


def test_contrastive_selection_preserves_negative_case_outside_naive_top_k() -> None:
    positive_one = ExperienceGuidance(
        memory_id="positive-1",
        polarity=ExperiencePolarity.POSITIVE,
        summary="positive",
        applicability=ExperienceApplicability(),
    )
    positive_two = ExperienceGuidance(
        memory_id="positive-2",
        polarity=ExperiencePolarity.POSITIVE,
        summary="positive",
    )
    negative = ExperienceGuidance(
        memory_id="negative-1",
        polarity=ExperiencePolarity.NEGATIVE,
        summary="negative",
    )

    selected = select_contrastive_experience(
        (positive_one, positive_two, negative),
        limit=2,
    )

    assert [item.memory_id for item in selected] == ["negative-1", "positive-1"]
