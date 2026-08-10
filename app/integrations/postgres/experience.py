import asyncio
import math
import re
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.models import (
    AgentResponse,
    ExperienceApplicability,
    ExperienceGuidance,
    ExperienceMemoryStatus,
    ExperienceMemoryUsage,
    ExperienceOutcome,
    ExperiencePolarity,
    ExperienceReleasePolicy,
    IssueResolutionStatus,
    OutcomeConflictStatus,
    OutcomeDecision,
    OutcomeEvidenceGrade,
    OutcomeSource,
)
from app.domain.ports import EmbeddingProviderPort
from app.domain.rules import (
    classify_conversation_intent,
    evaluate_experience_guardrails,
    extract_product_skus,
    merge_outcome_decision,
    redact_sensitive_text,
    select_contrastive_experience,
    should_continue_issue,
    should_quarantine_experience_memory,
)
from app.integrations.postgres.models import (
    AuditEventModel,
    ConversationSummarySegmentModel,
    ExperienceEvalRunModel,
    ExperienceEventModel,
    ExperienceExperimentModel,
    ExperienceMemoryUsageModel,
    ExperienceReleaseModel,
    ImprovementCandidateModel,
    IssueOutcomeEpisodeModel,
    TenantCaseMemoryModel,
    TenantPatternMemoryModel,
)

_POSITIVE_OUTCOMES = {
    ExperienceOutcome.SUCCESSFUL_SELF_SERVICE.value,
    ExperienceOutcome.SUCCESSFUL_HUMAN_RESOLUTION.value,
    ExperienceOutcome.CORRECT_POLICY_HANDOFF.value,
}
_NEGATIVE_OUTCOMES = {
    ExperienceOutcome.AVOIDABLE_HANDOFF.value,
    ExperienceOutcome.FAILED_ANSWER.value,
    ExperienceOutcome.REOPENED.value,
}


async def record_exchange_experience(
    session: AsyncSession,
    *,
    tenant_id: str,
    site_id: str,
    conversation_id: str,
    actor_cohort_hash: str,
    user_message: str,
    response: AgentResponse,
    occurred_at: datetime,
    customer_confirmed_resolution: bool,
    reopened: bool,
) -> None:
    intent = classify_conversation_intent(user_message)
    latest = await session.scalar(
        select(IssueOutcomeEpisodeModel)
        .where(
            IssueOutcomeEpisodeModel.tenant_id == tenant_id,
            IssueOutcomeEpisodeModel.conversation_id == conversation_id,
            IssueOutcomeEpisodeModel.status.in_(
                (
                    ExperienceOutcome.OPEN.value,
                    ExperienceOutcome.AWAITING_HUMAN_OUTCOME.value,
                    ExperienceOutcome.OBSERVING.value,
                )
            ),
        )
        .order_by(IssueOutcomeEpisodeModel.updated_at.desc())
        .limit(1)
        .with_for_update()
    )
    event_type = "conversation.ai_exchange"
    if reopened:
        event_type = "conversation.reopened"
        previous = await session.scalar(
            select(IssueOutcomeEpisodeModel)
            .where(
                IssueOutcomeEpisodeModel.tenant_id == tenant_id,
                IssueOutcomeEpisodeModel.conversation_id == conversation_id,
            )
            .order_by(IssueOutcomeEpisodeModel.updated_at.desc())
            .limit(1)
            .with_for_update()
        )
        if previous is not None:
            _apply_outcome_decision(
                previous,
                OutcomeDecision(
                    learning_outcome=ExperienceOutcome.REOPENED,
                    resolution_status=IssueResolutionStatus.REOPENED,
                    evidence_grade=OutcomeEvidenceGrade.STRONG,
                    source=OutcomeSource.REOPEN,
                ),
            )
            previous.outcome_observed_at = occurred_at
            previous.updated_at = occurred_at
            previous.revision += 1
            latest = None
    if customer_confirmed_resolution and latest is not None:
        _apply_outcome_decision(
            latest,
            OutcomeDecision(
                learning_outcome=ExperienceOutcome.SUCCESSFUL_SELF_SERVICE,
                resolution_status=IssueResolutionStatus.RESOLVED,
                evidence_grade=OutcomeEvidenceGrade.STRONG,
                source=OutcomeSource.CUSTOMER_CONFIRMATION,
            ),
        )
        latest.outcome_observed_at = occurred_at
        latest.updated_at = occurred_at
        latest.revision += 1
        event_type = "conversation.customer_confirmed_resolved"
    else:
        current_products = extract_product_skus(user_message)
        continues_issue = latest is not None and (
            latest.status == ExperienceOutcome.AWAITING_HUMAN_OUTCOME.value
            or should_continue_issue(
                previous_intent=latest.intent,
                current_intent=intent,
                message=user_message,
                previous_product_references=tuple(latest.product_references or ()),
                current_product_references=current_products,
                idle_seconds=max(0.0, (occurred_at - latest.updated_at).total_seconds()),
            )
        )
        if latest is None or not continues_issue:
            issue_id = str(
                uuid5(NAMESPACE_URL, f"{tenant_id}:{conversation_id}:{response.trace_id}")
            )
            latest = IssueOutcomeEpisodeModel(
                tenant_id=tenant_id,
                episode_id=str(uuid5(NAMESPACE_URL, f"{tenant_id}:episode:{issue_id}:1")),
                issue_id=issue_id,
                site_id=site_id,
                conversation_id=conversation_id,
                actor_cohort_hash=actor_cohort_hash,
                intent=intent,
                status=(
                    ExperienceOutcome.AWAITING_HUMAN_OUTCOME.value
                    if response.handoff_id
                    else ExperienceOutcome.OBSERVING.value
                ),
                handoff_id=response.handoff_id,
                trigger_trace_id=response.trace_id,
                source_message_ids=[],
                issue_summary=redact_sensitive_text(user_message)[:2000],
                ai_attempt_summary=redact_sensitive_text(response.message)[:2000],
                human_resolution_summary="",
                handoff_reason=response.handoff_reason,
                correction_labels=[],
                product_references=list(
                    dict.fromkeys(
                        (
                            *current_products,
                            *(item.sku for item in response.recommended_products),
                        )
                    )
                )[:10],
                evidence_references=list(response.evidence_ids)[:20],
                dependencies=_response_dependencies(response),
                outcome_evidence_grade=OutcomeEvidenceGrade.UNKNOWN.value,
                resolution_status=IssueResolutionStatus.OPEN.value,
                learning_outcome=ExperienceOutcome.UNKNOWN.value,
                outcome_source=OutcomeSource.UNKNOWN.value,
                outcome_conflict_status=OutcomeConflictStatus.NONE.value,
                revision=1,
                started_at=occurred_at,
                updated_at=occurred_at,
                outcome_observed_at=None,
            )
            session.add(latest)
        else:
            latest.issue_summary = _append_bounded(latest.issue_summary, user_message, 2000)
            latest.ai_attempt_summary = _append_bounded(
                latest.ai_attempt_summary, response.message, 2000
            )
            latest.updated_at = occurred_at
            latest.evidence_references = list(
                dict.fromkeys([*(latest.evidence_references or []), *response.evidence_ids])
            )[:20]
            latest.product_references = list(
                dict.fromkeys(
                    (
                        *(latest.product_references or []),
                        *current_products,
                        *(item.sku for item in response.recommended_products),
                    )
                )
            )[:10]
            latest.dependencies = _merge_dependencies(
                latest.dependencies or {},
                _response_dependencies(response),
            )
            if response.handoff_id:
                latest.status = ExperienceOutcome.AWAITING_HUMAN_OUTCOME.value
                latest.handoff_id = response.handoff_id
                latest.handoff_reason = response.handoff_reason
                event_type = "handoff.started"
        latest.source_message_ids = list(
            dict.fromkeys(
                [
                    *(latest.source_message_ids or []),
                    str(uuid5(NAMESPACE_URL, f"{response.trace_id}:user")),
                    str(uuid5(NAMESPACE_URL, f"{response.trace_id}:assistant")),
                ]
            )
        )[-50:]
        if response.handoff_id:
            latest.handoff_id = response.handoff_id
            latest.handoff_reason = response.handoff_reason
    _add_experience_event(
        session,
        tenant_id=tenant_id,
        site_id=site_id,
        conversation_id=conversation_id,
        issue_id=latest.issue_id if latest is not None else None,
        event_type=event_type,
        source="application",
        resource_type="conversation",
        resource_id=conversation_id,
        trace_id=response.trace_id,
        payload={
            "response_kind": response.kind.value,
            "risk_level": int(response.risk_level),
            "handoff_id": response.handoff_id,
            "knowledge_status": response.knowledge_status,
            "validation_reason": response.validation_reason,
            "evidence_ids": list(response.evidence_ids)[:20],
            "used_experience_ids": list(response.used_experience_ids)[:4],
        },
        occurred_at=occurred_at,
        idempotency_key=f"exchange:{response.trace_id}",
    )
    if latest is not None:
        await session.execute(
            update(ExperienceMemoryUsageModel)
            .where(
                ExperienceMemoryUsageModel.tenant_id == tenant_id,
                ExperienceMemoryUsageModel.trace_id == response.trace_id,
            )
            .values(issue_id=latest.issue_id)
        )
    if response.recommended_products:
        _add_experience_event(
            session,
            tenant_id=tenant_id,
            site_id=site_id,
            conversation_id=conversation_id,
            issue_id=latest.issue_id if latest is not None else None,
            event_type="recommendation.exposed",
            source="application",
            resource_type="conversation",
            resource_id=conversation_id,
            trace_id=response.trace_id,
            payload={
                "product_references": [item.sku for item in response.recommended_products[:3]],
                "comparison_requested": intent == "product_comparison",
            },
            occurred_at=occurred_at,
            idempotency_key=f"recommendation-exposure:{response.trace_id}",
        )


async def record_human_message_experience(
    session: AsyncSession,
    *,
    tenant_id: str,
    site_id: str,
    conversation_id: str,
    message_id: str,
    content: str,
    actor_subject_id: str,
    occurred_at: datetime,
) -> None:
    episode = await _latest_issue(session, tenant_id, conversation_id)
    if episode is None:
        return
    episode.human_resolution_summary = _append_bounded(
        episode.human_resolution_summary, content, 2500
    )
    episode.source_message_ids = list(
        dict.fromkeys([*(episode.source_message_ids or []), message_id])
    )[-50:]
    episode.correction_labels = list(
        dict.fromkeys([*(episode.correction_labels or []), "human_response_modified"])
    )
    episode.updated_at = occurred_at
    _add_experience_event(
        session,
        tenant_id=tenant_id,
        site_id=site_id,
        conversation_id=conversation_id,
        issue_id=episode.issue_id,
        event_type="human.reply_recorded",
        source="support_operations",
        resource_type="message",
        resource_id=message_id,
        trace_id=None,
        payload={"actor_subject_id": actor_subject_id, "content_stored": False},
        occurred_at=occurred_at,
        idempotency_key=f"human-reply:{message_id}",
    )


async def record_manual_resolution_experience(
    session: AsyncSession,
    *,
    tenant_id: str,
    site_id: str,
    conversation_id: str,
    actor_subject_id: str,
    occurred_at: datetime,
) -> None:
    episode = await _latest_issue(session, tenant_id, conversation_id)
    if episode is None:
        return
    mandatory = _mandatory_handoff_reason(episode.handoff_reason)
    previous_status = episode.status
    customer_requested = episode.handoff_reason == "human_requested"
    if mandatory:
        episode.status = ExperienceOutcome.CORRECT_POLICY_HANDOFF.value
    elif customer_requested:
        episode.status = ExperienceOutcome.SUCCESSFUL_HUMAN_RESOLUTION.value
    else:
        episode.status = ExperienceOutcome.AVOIDABLE_HANDOFF.value
        root_cause = _failure_label_from_handoff_reason(episode.handoff_reason)
        episode.correction_labels = list(
            dict.fromkeys([*(episode.correction_labels or []), root_cause])
        )
    _apply_outcome_decision(
        episode,
        OutcomeDecision(
            learning_outcome=ExperienceOutcome(episode.status),
            resolution_status=IssueResolutionStatus.RESOLVED,
            evidence_grade=OutcomeEvidenceGrade.MODERATE,
            source=OutcomeSource.HUMAN_VERIFIED,
        ),
    )
    episode.outcome_observed_at = occurred_at
    episode.updated_at = occurred_at
    episode.revision += 1
    _add_experience_event(
        session,
        tenant_id=tenant_id,
        site_id=site_id,
        conversation_id=conversation_id,
        issue_id=episode.issue_id,
        event_type="human.resolution_recorded",
        source="support_operations",
        resource_type="conversation",
        resource_id=conversation_id,
        trace_id=None,
        payload={
            "actor_subject_id": actor_subject_id,
            "mandatory_handoff": mandatory,
            "customer_requested": customer_requested,
            "outcome": episode.status,
            "previous_outcome": previous_status,
        },
        occurred_at=occurred_at,
        idempotency_key=f"manual-resolution:{episode.episode_id}:{episode.revision}",
    )


async def record_censored_resolution_experience(
    session: AsyncSession,
    *,
    tenant_id: str,
    site_id: str,
    conversation_id: str,
    occurred_at: datetime,
) -> None:
    episode = await _latest_issue(session, tenant_id, conversation_id)
    if episode is None:
        return
    previous_status = episode.status
    _apply_outcome_decision(
        episode,
        OutcomeDecision(
            learning_outcome=ExperienceOutcome.UNKNOWN,
            resolution_status=IssueResolutionStatus.CENSORED,
            evidence_grade=OutcomeEvidenceGrade.UNKNOWN,
            source=OutcomeSource.BEHAVIOR,
        ),
    )
    episode.outcome_observed_at = occurred_at
    episode.updated_at = occurred_at
    episode.revision += 1
    _add_experience_event(
        session,
        tenant_id=tenant_id,
        site_id=site_id,
        conversation_id=conversation_id,
        issue_id=episode.issue_id,
        event_type="conversation.inactivity_censored",
        source="auto_resolution",
        resource_type="conversation",
        resource_id=conversation_id,
        trace_id=None,
        payload={"success_inferred": False, "previous_outcome": previous_status},
        occurred_at=occurred_at,
        idempotency_key=f"inactivity-censored:{episode.episode_id}:{episode.revision}",
    )


async def record_satisfaction_experience(
    session: AsyncSession,
    *,
    tenant_id: str,
    site_id: str,
    conversation_id: str,
    rating_id: str,
    score: int,
    occurred_at: datetime,
) -> None:
    episode = await _latest_issue(session, tenant_id, conversation_id)
    if episode is None:
        return
    previous_status = episode.status
    if score >= 4:
        observed_outcome = (
            ExperienceOutcome.SUCCESSFUL_HUMAN_RESOLUTION.value
            if episode.handoff_id
            else ExperienceOutcome.SUCCESSFUL_SELF_SERVICE.value
        )
        resolution_status = IssueResolutionStatus.RESOLVED
        evidence_grade = OutcomeEvidenceGrade.STRONG
    elif score <= 2:
        observed_outcome = ExperienceOutcome.FAILED_ANSWER.value
        resolution_status = IssueResolutionStatus.FAILED
        evidence_grade = OutcomeEvidenceGrade.STRONG
    else:
        observed_outcome = ExperienceOutcome.UNKNOWN.value
        resolution_status = IssueResolutionStatus.OBSERVING
        evidence_grade = OutcomeEvidenceGrade.WEAK
    _apply_outcome_decision(
        episode,
        OutcomeDecision(
            learning_outcome=ExperienceOutcome(observed_outcome),
            resolution_status=resolution_status,
            evidence_grade=evidence_grade,
            source=OutcomeSource.CSAT,
        ),
    )
    episode.outcome_observed_at = occurred_at
    episode.updated_at = occurred_at
    episode.revision += 1
    _add_experience_event(
        session,
        tenant_id=tenant_id,
        site_id=site_id,
        conversation_id=conversation_id,
        issue_id=episode.issue_id,
        event_type="conversation.satisfaction_observed",
        source="customer_experience",
        resource_type="satisfaction_rating",
        resource_id=rating_id,
        trace_id=None,
        payload={
            "score": score,
            "comment_stored": False,
            "previous_outcome": previous_status,
        },
        occurred_at=occurred_at,
        idempotency_key=f"satisfaction:{rating_id}",
    )


async def _latest_issue(
    session: AsyncSession, tenant_id: str, conversation_id: str
) -> IssueOutcomeEpisodeModel | None:
    return await session.scalar(
        select(IssueOutcomeEpisodeModel)
        .where(
            IssueOutcomeEpisodeModel.tenant_id == tenant_id,
            IssueOutcomeEpisodeModel.conversation_id == conversation_id,
        )
        .order_by(IssueOutcomeEpisodeModel.updated_at.desc())
        .limit(1)
        .with_for_update()
    )


def _apply_outcome_decision(
    episode: IssueOutcomeEpisodeModel,
    incoming: OutcomeDecision,
) -> None:
    current = OutcomeDecision(
        learning_outcome=ExperienceOutcome(
            getattr(episode, "learning_outcome", None) or ExperienceOutcome.UNKNOWN.value
        ),
        resolution_status=IssueResolutionStatus(
            getattr(episode, "resolution_status", None) or IssueResolutionStatus.OPEN.value
        ),
        evidence_grade=OutcomeEvidenceGrade(
            episode.outcome_evidence_grade or OutcomeEvidenceGrade.UNKNOWN.value
        ),
        source=OutcomeSource(
            getattr(episode, "outcome_source", None) or OutcomeSource.UNKNOWN.value
        ),
        conflict_status=OutcomeConflictStatus(
            getattr(episode, "outcome_conflict_status", None) or OutcomeConflictStatus.NONE.value
        ),
    )
    decision = merge_outcome_decision(current, incoming)
    episode.learning_outcome = decision.learning_outcome.value
    episode.resolution_status = decision.resolution_status.value
    episode.outcome_evidence_grade = decision.evidence_grade.value
    episode.outcome_source = decision.source.value
    episode.outcome_conflict_status = decision.conflict_status.value
    if decision.learning_outcome is not ExperienceOutcome.UNKNOWN:
        episode.status = decision.learning_outcome.value
    elif decision.resolution_status is IssueResolutionStatus.CENSORED:
        episode.status = ExperienceOutcome.CENSORED.value
    else:
        episode.status = ExperienceOutcome.OBSERVING.value


def _add_experience_event(
    session: AsyncSession,
    *,
    tenant_id: str,
    site_id: str,
    conversation_id: str,
    issue_id: str | None,
    event_type: str,
    source: str,
    resource_type: str,
    resource_id: str,
    trace_id: str | None,
    payload: dict,
    occurred_at: datetime,
    idempotency_key: str,
) -> None:
    session.add(
        ExperienceEventModel(
            tenant_id=tenant_id,
            event_id=str(uuid5(NAMESPACE_URL, f"{tenant_id}:{idempotency_key}")),
            idempotency_key=idempotency_key,
            site_id=site_id,
            conversation_id=conversation_id,
            issue_id=issue_id,
            event_type=event_type,
            source=source,
            resource_type=resource_type,
            resource_id=resource_id,
            trace_id=trace_id,
            payload=payload,
            schema_version=1,
            retention_class="tenant_experience",
            occurred_at=occurred_at,
            created_at=occurred_at,
        )
    )


def _append_bounded(current: str, addition: str, limit: int) -> str:
    normalized = redact_sensitive_text(addition).strip()
    if not normalized:
        return current[:limit]
    return "\n".join(value for value in (current.strip(), normalized) if value)[:limit]


def _mandatory_handoff_reason(reason: str | None) -> bool:
    value = (reason or "").casefold()
    return value.startswith("order_") or value in {
        "high_impact_request",
        "severe_risk_detected",
        "knowledge_care_high_risk",
    }


def _failure_label_from_handoff_reason(reason: str | None) -> str:
    value = (reason or "unknown").casefold()
    if value in {"knowledge_insufficient", "knowledge_care_sop_missing"}:
        return "knowledge_missing"
    if value in {"knowledge_conflict", "knowledge_stale"}:
        return "knowledge_stale_or_conflicting"
    if value in {"knowledge_tool_failure", "business_tool_failed"}:
        return "tool_failure"
    if value.startswith("knowledge_"):
        return "retrieval_miss"
    if value == "response_validation_failed":
        return "unsupported_claim"
    return "unknown"


class PostgreSQLTenantExperienceAdapter:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        embedding_provider: EmbeddingProviderPort,
        *,
        embedding_dimension: int = 384,
        minimum_similarity: float = 0.35,
        retrieval_timeout_seconds: float = 0.15,
        case_ttl_days: int = 90,
        outcome_observation_days: int = 7,
    ) -> None:
        self._session_factory = session_factory
        self._embedding_provider = embedding_provider
        self._embedding_dimension = embedding_dimension
        self._minimum_similarity = minimum_similarity
        self._retrieval_timeout_seconds = retrieval_timeout_seconds
        self._case_ttl_days = case_ttl_days
        self._outcome_observation_days = outcome_observation_days

    async def retrieve(
        self,
        *,
        tenant_id: str,
        site_id: str,
        query: str,
        intent: str | None,
        product_references: tuple[str, ...],
        risk_level: int,
        release_version: str,
        include_shadow: bool,
        limit: int,
    ) -> tuple[ExperienceGuidance, ...]:
        now = datetime.now(UTC)
        statuses = [
            ExperienceMemoryStatus.OPERATIONAL_ACTIVE.value,
            ExperienceMemoryStatus.APPROVED_FACTUAL.value,
        ]
        if include_shadow:
            statuses.append(ExperienceMemoryStatus.SHADOW.value)
        vector = await self._query_vector(query)
        async with self._session_factory() as session:
            release = await session.scalar(
                select(ExperienceReleaseModel).where(
                    ExperienceReleaseModel.tenant_id == tenant_id,
                    ExperienceReleaseModel.release_version == release_version,
                )
            )
            if release is not None and release.status in {"rolled_back", "rejected"}:
                return ()
            manifest = dict(release.manifest or {}) if release is not None else {}
            if (
                release is not None
                and release.mode == "active"
                and not _manifest_allows_request(
                    manifest,
                    site_id=site_id,
                    intent=intent,
                    risk_level=risk_level,
                )
            ):
                return ()
            statement = (
                select(TenantCaseMemoryModel)
                .where(
                    TenantCaseMemoryModel.tenant_id == tenant_id,
                    TenantCaseMemoryModel.site_id == site_id,
                    TenantCaseMemoryModel.status.in_(statuses),
                    TenantCaseMemoryModel.freshness == "current",
                    or_(
                        TenantCaseMemoryModel.expires_at.is_(None),
                        TenantCaseMemoryModel.expires_at > now,
                    ),
                )
                .limit(50)
            )
            if vector is not None:
                statement = statement.order_by(
                    TenantCaseMemoryModel.embedding.cosine_distance(vector)
                )
            else:
                statement = statement.order_by(TenantCaseMemoryModel.updated_at.desc())
            models = list(await session.scalars(statement))
            patterns = list(
                await session.scalars(
                    select(TenantPatternMemoryModel)
                    .where(
                        TenantPatternMemoryModel.tenant_id == tenant_id,
                        or_(
                            TenantPatternMemoryModel.site_id.is_(None),
                            TenantPatternMemoryModel.site_id == site_id,
                        ),
                        TenantPatternMemoryModel.status.in_(statuses),
                        TenantPatternMemoryModel.needs_rebuild.is_(False),
                    )
                    .order_by(TenantPatternMemoryModel.updated_at.desc())
                    .limit(20)
                )
            )
        ranked: list[tuple[float, ExperienceGuidance]] = []
        for model in models:
            if not _manifest_allows_memory(manifest, model.memory_id):
                continue
            applicability = _applicability(model.applicability)
            if not applicability.minimum_risk <= risk_level <= applicability.maximum_risk:
                continue
            semantic = _semantic_score(query, model.summary, vector, model.embedding)
            intent_score = (
                1.0
                if intent and intent in applicability.intents
                else 0.5
                if not applicability.intents
                else 0.0
            )
            product_score = _set_overlap(product_references, applicability.product_references)
            reliability = max(0.0, min(1.0, float(model.wilson_lower_bound)))
            recency = _recency_score(model.updated_at, now)
            score = (
                0.35 * semantic
                + 0.25 * intent_score
                + 0.15 * product_score
                + 0.20 * reliability
                + 0.05 * recency
            )
            if score < self._minimum_similarity:
                continue
            ranked.append((score, _guidance(model, applicability)))
        for pattern in patterns:
            if not _manifest_allows_memory(manifest, pattern.pattern_id):
                continue
            guidance = _pattern_guidance(pattern)
            applicability = guidance.applicability
            semantic = _semantic_score(query, guidance.summary, vector, None)
            intent_score = (
                1.0
                if intent and intent in applicability.intents
                else 0.5
                if not applicability.intents
                else 0.0
            )
            product_score = _set_overlap(product_references, applicability.product_references)
            score = (
                0.35 * semantic
                + 0.25 * intent_score
                + 0.15 * product_score
                + 0.20 * guidance.wilson_lower_bound
                + 0.05 * _recency_score(pattern.updated_at, now)
            )
            if score >= self._minimum_similarity:
                ranked.append((score, guidance))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return select_contrastive_experience(
            tuple(guidance for _, guidance in ranked),
            limit,
        )

    async def record_usage(self, usage: ExperienceMemoryUsage) -> None:
        await self.record_usages((usage,))

    async def record_usages(self, usages: tuple[ExperienceMemoryUsage, ...]) -> None:
        if not usages:
            return
        tenant_ids = {usage.tenant_id for usage in usages}
        if len(tenant_ids) != 1:
            raise ValueError("usage batch must belong to exactly one tenant")
        async with self._session_factory.begin() as session:
            for usage in usages:
                assignment_key = (
                    f"{usage.tenant_id}:{usage.conversation_id}:{usage.release_version}"
                )
                assignment_id = str(uuid5(NAMESPACE_URL, f"experience-assignment:{assignment_key}"))
                await session.execute(
                    pg_insert(ExperienceExperimentModel)
                    .values(
                        tenant_id=usage.tenant_id,
                        assignment_id=assignment_id,
                        conversation_id=usage.conversation_id,
                        issue_id=usage.issue_id,
                        release_version=usage.release_version,
                        treatment=usage.treatment,
                        assignment_key_hash=sha256(assignment_key.encode()).hexdigest(),
                        assigned_at=usage.occurred_at,
                    )
                    .on_conflict_do_nothing(
                        index_elements=("tenant_id", "conversation_id", "release_version")
                    )
                )
                await session.execute(
                    pg_insert(ExperienceMemoryUsageModel)
                    .values(
                        tenant_id=usage.tenant_id,
                        usage_id=usage.usage_id,
                        conversation_id=usage.conversation_id,
                        issue_id=usage.issue_id,
                        trace_id=usage.trace_id,
                        memory_id=usage.memory_id,
                        release_version=usage.release_version,
                        treatment=usage.treatment,
                        retrieved=usage.retrieved,
                        eligible=usage.eligible,
                        selected=usage.selected,
                        influenced=usage.influenced,
                        outcome_attributed=usage.outcome_attributed,
                        used_for=list(usage.used_for),
                        occurred_at=usage.occurred_at,
                    )
                    .on_conflict_do_nothing(index_elements=("tenant_id", "usage_id"))
                )

    async def get_release_policy(
        self,
        *,
        tenant_id: str,
        release_version: str,
    ) -> ExperienceReleasePolicy | None:
        async with self._session_factory() as session:
            release = await session.scalar(
                select(ExperienceReleaseModel).where(
                    ExperienceReleaseModel.tenant_id == tenant_id,
                    ExperienceReleaseModel.release_version == release_version,
                )
            )
        if release is None:
            return None
        return ExperienceReleasePolicy(
            release_version=release.release_version,
            mode=release.mode,
            rollout_percent=release.rollout_percent,
            status=release.status,
            included_memory_ids=tuple(
                str(value) for value in (release.manifest or {}).get("included_memory_ids", [])
            ),
            excluded_memory_ids=tuple(
                str(value) for value in (release.manifest or {}).get("excluded_memory_ids", [])
            ),
            eligible_intents=tuple(
                str(value) for value in (release.manifest or {}).get("eligible_intents", [])
            ),
            eligible_sites=tuple(
                str(value) for value in (release.manifest or {}).get("eligible_sites", [])
            ),
            risk_ceiling=int((release.manifest or {}).get("risk_ceiling", 1)),
            dataset_version=release.dataset_version,
        )

    async def build_pending_case_memories(
        self,
        *,
        tenant_id: str,
        evaluated_at: datetime,
        limit: int,
    ) -> int:
        built = 0
        async with self._session_factory.begin() as session:
            episodes = list(
                await session.scalars(
                    select(IssueOutcomeEpisodeModel)
                    .where(
                        IssueOutcomeEpisodeModel.tenant_id == tenant_id,
                        IssueOutcomeEpisodeModel.status.in_(
                            tuple(_POSITIVE_OUTCOMES | _NEGATIVE_OUTCOMES)
                        ),
                        ~select(TenantCaseMemoryModel.id)
                        .where(
                            TenantCaseMemoryModel.tenant_id == tenant_id,
                            TenantCaseMemoryModel.source_episode_id
                            == IssueOutcomeEpisodeModel.episode_id,
                            TenantCaseMemoryModel.updated_at >= IssueOutcomeEpisodeModel.updated_at,
                        )
                        .exists(),
                    )
                    .order_by(IssueOutcomeEpisodeModel.updated_at)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            for episode in episodes:
                summary = _case_summary(episode)
                vector = await self._query_vector(summary)
                memory_id = str(uuid5(NAMESPACE_URL, f"{tenant_id}:case:{episode.episode_id}"))
                polarity = (
                    ExperiencePolarity.POSITIVE
                    if episode.status in _POSITIVE_OUTCOMES
                    else ExperiencePolarity.NEGATIVE
                )
                guidance = _guidance_payload(episode, polarity)
                sample_count = 1
                initial_success = 1 if polarity is ExperiencePolarity.POSITIVE else 0
                lower_bound = _wilson_lower_bound(initial_success, sample_count)
                memory = await session.scalar(
                    select(TenantCaseMemoryModel)
                    .where(
                        TenantCaseMemoryModel.tenant_id == tenant_id,
                        TenantCaseMemoryModel.source_episode_id == episode.episode_id,
                    )
                    .with_for_update()
                )
                values = {
                    "site_id": episode.site_id,
                    "source_actor_cohort_hash": episode.actor_cohort_hash,
                    "issue_signature": sha256(
                        f"{episode.intent}:{summary.casefold()}".encode()
                    ).hexdigest(),
                    "polarity": polarity.value,
                    "summary": summary,
                    "guidance": guidance,
                    "applicability": {
                        "intents": [episode.intent],
                        "product_references": list(episode.product_references or []),
                        "materials": [],
                        "regions": [],
                        "minimum_risk": 0,
                        "maximum_risk": 1,
                        "exclusions": ["authoritative_fact_source"],
                    },
                    "dependencies": dict(episode.dependencies or {}),
                    "outcome_evidence_grade": episode.outcome_evidence_grade,
                    "freshness": "current",
                    "status": ExperienceMemoryStatus.SHADOW.value,
                    "sample_count": sample_count,
                    "success_count": initial_success,
                    "failure_count": 1 - initial_success,
                    "negative_transfer_count": 0,
                    "uncertainty": 1.0,
                    "wilson_lower_bound": lower_bound,
                    "embedding": vector,
                    "updated_at": evaluated_at,
                    "expires_at": evaluated_at + timedelta(days=self._case_ttl_days),
                }
                if memory is None:
                    memory = TenantCaseMemoryModel(
                        tenant_id=tenant_id,
                        memory_id=memory_id,
                        source_episode_id=episode.episode_id,
                        created_at=evaluated_at,
                        **values,
                    )
                    session.add(memory)
                    event_type = "tenant_case_memory.created"
                else:
                    for key, value in values.items():
                        setattr(memory, key, value)
                    event_type = "tenant_case_memory.rebuilt"
                session.add(
                    AuditEventModel(
                        tenant_id=tenant_id,
                        event_id=str(uuid4()),
                        event_type=event_type,
                        actor_subject_id="tenant-experience-worker",
                        resource_type="tenant_case_memory",
                        resource_id=memory_id,
                        details={
                            "source_episode_id": episode.episode_id,
                            "polarity": polarity.value,
                            "status": ExperienceMemoryStatus.SHADOW.value,
                        },
                        created_at=evaluated_at,
                    )
                )
                built += 1
        return built

    async def reconcile_delayed_outcomes(
        self,
        *,
        tenant_id: str,
        evaluated_at: datetime,
        limit: int,
    ) -> int:
        reconciled = 0
        cutoff = evaluated_at - timedelta(days=self._outcome_observation_days)
        async with self._session_factory.begin() as session:
            censored = list(
                await session.scalars(
                    select(IssueOutcomeEpisodeModel)
                    .where(
                        IssueOutcomeEpisodeModel.tenant_id == tenant_id,
                        IssueOutcomeEpisodeModel.status.in_(
                            (
                                ExperienceOutcome.OPEN.value,
                                ExperienceOutcome.OBSERVING.value,
                            )
                        ),
                        IssueOutcomeEpisodeModel.updated_at <= cutoff,
                    )
                    .order_by(IssueOutcomeEpisodeModel.updated_at)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            for episode in censored:
                _apply_outcome_decision(
                    episode,
                    OutcomeDecision(
                        learning_outcome=ExperienceOutcome.UNKNOWN,
                        resolution_status=IssueResolutionStatus.CENSORED,
                        evidence_grade=OutcomeEvidenceGrade.UNKNOWN,
                        source=OutcomeSource.BEHAVIOR,
                    ),
                )
                episode.outcome_observed_at = evaluated_at
                episode.updated_at = evaluated_at
                reconciled += 1

            remaining = max(0, limit - reconciled)
            if remaining:
                usages = list(
                    await session.scalars(
                        select(ExperienceMemoryUsageModel)
                        .where(
                            ExperienceMemoryUsageModel.tenant_id == tenant_id,
                            ExperienceMemoryUsageModel.outcome.is_(None),
                        )
                        .order_by(ExperienceMemoryUsageModel.occurred_at)
                        .limit(remaining)
                        .with_for_update(skip_locked=True)
                    )
                )
                for usage in usages:
                    episode_filter = (
                        IssueOutcomeEpisodeModel.issue_id == usage.issue_id
                        if usage.issue_id
                        else IssueOutcomeEpisodeModel.conversation_id == usage.conversation_id
                    )
                    episode = await session.scalar(
                        select(IssueOutcomeEpisodeModel)
                        .where(
                            IssueOutcomeEpisodeModel.tenant_id == tenant_id,
                            episode_filter,
                            IssueOutcomeEpisodeModel.status.in_(
                                tuple(_POSITIVE_OUTCOMES | _NEGATIVE_OUTCOMES)
                            ),
                            IssueOutcomeEpisodeModel.updated_at >= usage.occurred_at,
                        )
                        .order_by(IssueOutcomeEpisodeModel.updated_at.desc())
                        .limit(1)
                    )
                    if episode is None:
                        continue
                    usage.outcome = episode.status
                    usage.outcome_observed_at = evaluated_at
                    usage.outcome_attributed = True
                    memory = await session.scalar(
                        select(TenantCaseMemoryModel)
                        .where(
                            TenantCaseMemoryModel.tenant_id == tenant_id,
                            TenantCaseMemoryModel.memory_id == usage.memory_id,
                        )
                        .with_for_update()
                    )
                    if memory is not None and usage.influenced:
                        success = episode.status in _POSITIVE_OUTCOMES
                        memory.sample_count += 1
                        memory.success_count += int(success)
                        memory.failure_count += int(not success)
                        memory.negative_transfer_count += int(not success)
                        memory.uncertainty = 1 / math.sqrt(memory.sample_count)
                        memory.wilson_lower_bound = _wilson_lower_bound(
                            memory.success_count, memory.sample_count
                        )
                        memory.updated_at = evaluated_at
                    reconciled += 1
        return reconciled

    async def consolidate_patterns(
        self,
        *,
        tenant_id: str,
        evaluated_at: datetime,
        minimum_cluster_size: int,
        limit: int,
    ) -> int:
        if minimum_cluster_size < 2:
            raise ValueError("minimum cluster size must be at least 2")
        async with self._session_factory.begin() as session:
            cases = list(
                await session.scalars(
                    select(TenantCaseMemoryModel)
                    .where(
                        TenantCaseMemoryModel.tenant_id == tenant_id,
                        TenantCaseMemoryModel.status.in_(
                            (
                                ExperienceMemoryStatus.SHADOW.value,
                                ExperienceMemoryStatus.OPERATIONAL_ACTIVE.value,
                                ExperienceMemoryStatus.APPROVED_FACTUAL.value,
                            )
                        ),
                        TenantCaseMemoryModel.freshness == "current",
                        or_(
                            TenantCaseMemoryModel.expires_at.is_(None),
                            TenantCaseMemoryModel.expires_at > evaluated_at,
                        ),
                    )
                    .order_by(TenantCaseMemoryModel.updated_at.desc())
                    .limit(limit)
                )
            )
            grouped: dict[tuple[str, str, str, str], list[TenantCaseMemoryModel]] = {}
            for memory in cases:
                applicability = dict(memory.applicability or {})
                guidance = dict(memory.guidance or {})
                intents = [str(value) for value in applicability.get("intents", [])]
                avoid_actions = [str(value) for value in guidance.get("avoid_actions", [])]
                key = (
                    memory.site_id,
                    intents[0] if intents else "unknown",
                    memory.polarity,
                    str(
                        guidance.get("root_cause")
                        or (avoid_actions[0] if avoid_actions else "outcome_pattern")
                    ),
                )
                grouped.setdefault(key, []).append(memory)

            changed = 0
            for (site_id, intent, polarity, root_cause), members in grouped.items():
                unique_members = _one_case_per_actor(members)
                if len(unique_members) < minimum_cluster_size:
                    continue
                fingerprint = sha256(
                    f"{tenant_id}:{site_id}:{intent}:{polarity}:{root_cause}".encode()
                ).hexdigest()
                pattern_id = str(uuid5(NAMESPACE_URL, f"{tenant_id}:pattern:{fingerprint}"))
                source_ids = sorted({item.memory_id for item in unique_members})
                guidance = _aggregate_pattern_guidance(
                    unique_members,
                    intent=intent,
                    root_cause=root_cause,
                )
                existing = await session.scalar(
                    select(TenantPatternMemoryModel)
                    .where(
                        TenantPatternMemoryModel.tenant_id == tenant_id,
                        TenantPatternMemoryModel.fingerprint == fingerprint,
                    )
                    .with_for_update()
                )
                if existing is None:
                    session.add(
                        TenantPatternMemoryModel(
                            tenant_id=tenant_id,
                            pattern_id=pattern_id,
                            site_id=site_id,
                            fingerprint=fingerprint,
                            guidance=guidance,
                            source_memory_ids=source_ids,
                            status=ExperienceMemoryStatus.SHADOW.value,
                            needs_rebuild=False,
                            created_at=evaluated_at,
                            updated_at=evaluated_at,
                        )
                    )
                else:
                    existing.guidance = guidance
                    existing.source_memory_ids = source_ids
                    existing.needs_rebuild = False
                    existing.updated_at = evaluated_at
                changed += 1
            return changed

    async def generate_improvement_candidates(
        self,
        *,
        tenant_id: str,
        evaluated_at: datetime,
        limit: int,
    ) -> int:
        async with self._session_factory.begin() as session:
            patterns = list(
                await session.scalars(
                    select(TenantPatternMemoryModel)
                    .where(
                        TenantPatternMemoryModel.tenant_id == tenant_id,
                        TenantPatternMemoryModel.status.in_(
                            (
                                ExperienceMemoryStatus.SHADOW.value,
                                ExperienceMemoryStatus.OPERATIONAL_ACTIVE.value,
                            )
                        ),
                        TenantPatternMemoryModel.needs_rebuild.is_(False),
                    )
                    .order_by(TenantPatternMemoryModel.updated_at.desc())
                    .limit(limit)
                )
            )
            created = 0
            for pattern in patterns:
                guidance = dict(pattern.guidance or {})
                if guidance.get("polarity") != ExperiencePolarity.NEGATIVE.value:
                    continue
                root_cause = str(guidance.get("root_cause") or "unknown")
                candidate_id = str(
                    uuid5(NAMESPACE_URL, f"{tenant_id}:candidate:{pattern.pattern_id}")
                )
                existing = await session.scalar(
                    select(ImprovementCandidateModel.id).where(
                        ImprovementCandidateModel.tenant_id == tenant_id,
                        ImprovementCandidateModel.candidate_id == candidate_id,
                    )
                )
                if existing is not None:
                    continue
                candidate_type = _improvement_candidate_type(root_cause)
                session.add(
                    ImprovementCandidateModel(
                        tenant_id=tenant_id,
                        candidate_id=candidate_id,
                        candidate_type=candidate_type,
                        root_cause=root_cause,
                        source_pattern_ids=[pattern.pattern_id],
                        proposed_change={
                            "first_action": "create_or_update_evaluation_case",
                            "target_asset_type": candidate_type,
                            "guidance": guidance,
                            "automatic_production_change_allowed": False,
                        },
                        authoritative_source_refs=[],
                        status="draft",
                        created_at=evaluated_at,
                        updated_at=evaluated_at,
                    )
                )
                created += 1
            return created

    async def evaluate_release_gates(
        self,
        *,
        tenant_id: str,
        release_version: str,
        evaluated_at: datetime,
        minimum_samples: int,
    ) -> dict[str, object]:
        async with self._session_factory.begin() as session:
            eval_run = await session.scalar(
                select(ExperienceEvalRunModel)
                .where(
                    ExperienceEvalRunModel.tenant_id == tenant_id,
                    ExperienceEvalRunModel.release_version == release_version,
                    ExperienceEvalRunModel.status == "completed",
                )
                .order_by(ExperienceEvalRunModel.created_at.desc())
                .limit(1)
            )
            metrics = dict(eval_run.metrics or {}) if eval_run is not None else {}
            sample_count = int(eval_run.sample_count) if eval_run is not None else 0
            checks = {
                "metrics_schema": int(metrics.get("metrics_schema_version", 0)) >= 2,
                "dataset_fingerprint": bool(
                    re.fullmatch(r"[0-9a-f]{64}", str(metrics.get("dataset_fingerprint", "")))
                ),
                "deduplicated_samples": (
                    int(metrics.get("deduplicated_case_count", -1)) == sample_count
                ),
                "segment_sample_floor": int(metrics.get("minimum_segment_samples", 0)) >= 10,
                "issue_segmentation": (
                    float(metrics.get("issue_segmentation_accuracy", 0.0)) >= 0.95
                ),
                "outcome_labeling": (float(metrics.get("outcome_label_accuracy", 0.0)) >= 0.95),
                "baseline_declared": bool(str(metrics.get("baseline_release_version", "")).strip()),
                "evaluated_memories_declared": bool(metrics.get("evaluated_memory_ids")),
                "cross_tenant_leakage": int(metrics.get("cross_tenant_leakage", 0)) == 0,
                "stale_memory_usage": int(metrics.get("stale_memory_usage", 0)) == 0,
                "unredacted_experience": int(metrics.get("unredacted_experience", 0)) == 0,
                "shadow_precision_at_1": float(metrics.get("precision_at_1", 0.0)) >= 0.80,
                "negative_transfer_rate": (
                    float(metrics.get("negative_transfer_rate", 1.0)) < 0.01
                ),
                "correct_handoff_recall": (
                    float(metrics.get("correct_handoff_recall_delta", -1.0)) >= 0.0
                ),
                "reopen_rate": float(metrics.get("reopen_rate_delta", 1.0)) <= 0.0,
                "human_correction_rate": (
                    float(metrics.get("human_correction_rate_delta", 1.0)) <= 0.0
                ),
                "retrieval_p95_ms": float(metrics.get("retrieval_p95_ms", 999999.0)) < 150.0,
                "deletion_propagation": (
                    float(metrics.get("deletion_propagation_rate", 0.0)) >= 1.0
                ),
            }
            if sample_count < minimum_samples:
                status = "insufficient_samples"
            elif all(checks.values()):
                status = "passed"
            else:
                status = "failed"
            report: dict[str, object] = {
                "status": status,
                "sample_count": sample_count,
                "minimum_samples": minimum_samples,
                "checks": checks,
                "metrics": metrics,
                "evaluated_at": evaluated_at.isoformat(),
                "eval_run_id": eval_run.eval_run_id if eval_run is not None else None,
            }
            release = await session.scalar(
                select(ExperienceReleaseModel)
                .where(
                    ExperienceReleaseModel.tenant_id == tenant_id,
                    ExperienceReleaseModel.release_version == release_version,
                )
                .with_for_update()
            )
            if release is None:
                session.add(
                    ExperienceReleaseModel(
                        tenant_id=tenant_id,
                        release_version=release_version,
                        mode="shadow",
                        rollout_percent=0,
                        status=status,
                        gate_report=report,
                        created_at=evaluated_at,
                    )
                )
            else:
                release.gate_report = report
                release.status = status
            return report

    async def record_eval_run(
        self,
        *,
        tenant_id: str,
        eval_run_id: str,
        release_version: str,
        evaluation_mode: str,
        status: str,
        metrics: dict[str, object],
        sample_count: int,
        dataset_version: str,
        created_at: datetime,
    ) -> None:
        if evaluation_mode not in {"offline", "shadow", "replay"}:
            raise ValueError("unsupported evaluation mode")
        if status not in {"completed", "failed"}:
            raise ValueError("unsupported evaluation status")
        self._validate_eval_metrics(metrics)
        if not dataset_version.strip():
            raise ValueError("experience evaluation dataset version is required")
        if int(metrics["deduplicated_case_count"]) != sample_count:
            raise ValueError("sample count must equal the deduplicated evaluation case count")
        async with self._session_factory.begin() as session:
            await session.execute(
                pg_insert(ExperienceEvalRunModel)
                .values(
                    tenant_id=tenant_id,
                    eval_run_id=eval_run_id,
                    release_version=release_version,
                    evaluation_mode=evaluation_mode,
                    status=status,
                    metrics=metrics,
                    sample_count=max(0, sample_count),
                    dataset_version=dataset_version,
                    created_at=created_at,
                )
                .on_conflict_do_nothing(index_elements=("tenant_id", "eval_run_id"))
            )

    async def activate_release(
        self,
        *,
        tenant_id: str,
        release_version: str,
        approved_by: str,
        rollout_percent: int,
        included_memory_ids: tuple[str, ...],
        excluded_memory_ids: tuple[str, ...],
        eligible_intents: tuple[str, ...],
        eligible_sites: tuple[str, ...],
        risk_ceiling: int,
        dataset_version: str,
        activated_at: datetime,
    ) -> int:
        if not approved_by.strip():
            raise ValueError("approved_by is required")
        if not 0 <= rollout_percent <= 100:
            raise ValueError("rollout percent must be between 0 and 100")
        if risk_ceiling not in {0, 1}:
            raise ValueError("experience release risk ceiling must be 0 or 1")
        async with self._session_factory.begin() as session:
            release = await session.scalar(
                select(ExperienceReleaseModel)
                .where(
                    ExperienceReleaseModel.tenant_id == tenant_id,
                    ExperienceReleaseModel.release_version == release_version,
                )
                .with_for_update()
            )
            if release is None or (release.gate_report or {}).get("status") != "passed":
                raise ValueError("release gates have not passed")
            if release.status == "active" or release.manifest:
                raise ValueError("an activated experience release is immutable")
            eval_run = await session.scalar(
                select(ExperienceEvalRunModel)
                .where(
                    ExperienceEvalRunModel.tenant_id == tenant_id,
                    ExperienceEvalRunModel.release_version == release_version,
                    ExperienceEvalRunModel.status == "completed",
                )
                .order_by(ExperienceEvalRunModel.created_at.desc())
                .limit(1)
            )
            if eval_run is None:
                raise ValueError("a completed evaluation run is required")
            selected_dataset_version = dataset_version.strip() or eval_run.dataset_version
            if selected_dataset_version != eval_run.dataset_version:
                raise ValueError("release dataset version does not match the passed evaluation")
            memories = list(
                await session.scalars(
                    select(TenantCaseMemoryModel)
                    .where(
                        TenantCaseMemoryModel.tenant_id == tenant_id,
                        TenantCaseMemoryModel.status.in_(
                            (
                                ExperienceMemoryStatus.SHADOW.value,
                                ExperienceMemoryStatus.OPERATIONAL_ACTIVE.value,
                            )
                        ),
                        TenantCaseMemoryModel.freshness == "current",
                    )
                    .with_for_update()
                )
            )
            patterns = list(
                await session.scalars(
                    select(TenantPatternMemoryModel)
                    .where(
                        TenantPatternMemoryModel.tenant_id == tenant_id,
                        TenantPatternMemoryModel.status.in_(
                            (
                                ExperienceMemoryStatus.SHADOW.value,
                                ExperienceMemoryStatus.OPERATIONAL_ACTIVE.value,
                            )
                        ),
                        TenantPatternMemoryModel.needs_rebuild.is_(False),
                    )
                    .with_for_update()
                )
            )
            available = {item.memory_id for item in memories} | {
                item.pattern_id for item in patterns
            }
            requested = set(_normalized_values(included_memory_ids))
            excluded = set(_normalized_values(excluded_memory_ids))
            unknown = requested - available
            if unknown:
                raise ValueError("release includes unavailable experience memories")
            selected_ids = (requested or available) - excluded
            if not selected_ids:
                raise ValueError("experience release must include at least one memory")
            evaluated_ids = {
                str(value) for value in (eval_run.metrics or {}).get("evaluated_memory_ids", [])
            }
            if not selected_ids.issubset(evaluated_ids):
                raise ValueError("release includes experience memories absent from evaluation")
            for memory in memories:
                if memory.memory_id not in selected_ids:
                    continue
                memory.status = ExperienceMemoryStatus.OPERATIONAL_ACTIVE.value
                memory.updated_at = activated_at
            for pattern in patterns:
                if pattern.pattern_id not in selected_ids:
                    continue
                pattern.status = ExperienceMemoryStatus.OPERATIONAL_ACTIVE.value
                pattern.updated_at = activated_at
            release.manifest = {
                "schema_version": 1,
                "included_memory_ids": sorted(selected_ids),
                "excluded_memory_ids": sorted(excluded),
                "eligible_intents": list(_normalized_values(eligible_intents)),
                "eligible_sites": list(_normalized_values(eligible_sites)),
                "risk_ceiling": risk_ceiling,
                "dataset_version": selected_dataset_version,
                "created_at": activated_at.isoformat(),
            }
            release.dataset_version = selected_dataset_version
            release.mode = "active"
            release.rollout_percent = rollout_percent
            release.status = "active"
            release.approved_by = approved_by
            release.activated_at = activated_at
            release.rolled_back_at = None
            release.paused_at = None
            return len(selected_ids)

    async def enforce_online_guardrails(
        self,
        *,
        tenant_id: str,
        release_version: str,
        evaluated_at: datetime,
        minimum_samples: int,
        maximum_negative_transfer_rate: float,
        maximum_failure_rate_delta: float,
    ) -> dict[str, object]:
        if minimum_samples < 1:
            raise ValueError("guardrail minimum samples must be positive")
        if not 0 <= maximum_negative_transfer_rate <= 1:
            raise ValueError("maximum negative transfer rate must be between 0 and 1")
        if not 0 <= maximum_failure_rate_delta <= 1:
            raise ValueError("maximum failure rate delta must be between 0 and 1")
        async with self._session_factory.begin() as session:
            release = await session.scalar(
                select(ExperienceReleaseModel)
                .where(
                    ExperienceReleaseModel.tenant_id == tenant_id,
                    ExperienceReleaseModel.release_version == release_version,
                )
                .with_for_update()
            )
            if release is None or release.status != "active":
                return {"status": "not_active", "paused": False, "quarantined_memory_ids": []}
            usages = list(
                await session.scalars(
                    select(ExperienceMemoryUsageModel).where(
                        ExperienceMemoryUsageModel.tenant_id == tenant_id,
                        ExperienceMemoryUsageModel.release_version == release_version,
                        ExperienceMemoryUsageModel.outcome_attributed.is_(True),
                    )
                )
            )
            trace_outcomes: dict[tuple[str, str], str] = {}
            memory_outcomes: dict[str, dict[str, str]] = {}
            for usage in usages:
                if not usage.outcome:
                    continue
                trace_outcomes[(usage.treatment, usage.trace_id)] = usage.outcome
                if usage.influenced:
                    memory_outcomes.setdefault(usage.memory_id, {})[usage.trace_id] = usage.outcome
            treatment = [
                outcome for (cohort, _), outcome in trace_outcomes.items() if cohort == "treatment"
            ]
            control = [
                outcome for (cohort, _), outcome in trace_outcomes.items() if cohort == "control"
            ]
            guardrail = evaluate_experience_guardrails(
                treatment_outcomes=tuple(ExperienceOutcome(value) for value in treatment),
                control_outcomes=tuple(ExperienceOutcome(value) for value in control),
                minimum_samples=minimum_samples,
                maximum_failure_rate_delta=maximum_failure_rate_delta,
            )

            quarantine_minimum = max(10, minimum_samples // 3)
            quarantined: list[str] = []
            for memory_id, outcomes_by_trace in memory_outcomes.items():
                outcomes = list(outcomes_by_trace.values())
                if not should_quarantine_experience_memory(
                    outcomes=tuple(ExperienceOutcome(value) for value in outcomes),
                    minimum_samples=quarantine_minimum,
                    maximum_negative_transfer_rate=maximum_negative_transfer_rate,
                ):
                    continue
                memory = await session.scalar(
                    select(TenantCaseMemoryModel)
                    .where(
                        TenantCaseMemoryModel.tenant_id == tenant_id,
                        TenantCaseMemoryModel.memory_id == memory_id,
                    )
                    .with_for_update()
                )
                if memory is not None:
                    memory.status = ExperienceMemoryStatus.INACTIVE.value
                    memory.updated_at = evaluated_at
                    quarantined.append(memory_id)

            pause = guardrail.pause_release
            report: dict[str, object] = {
                "status": guardrail.status,
                "paused": pause,
                "treatment_samples": guardrail.treatment_samples,
                "control_samples": guardrail.control_samples,
                "treatment_failure_rate": guardrail.treatment_failure_rate,
                "control_failure_rate": guardrail.control_failure_rate,
                "failure_rate_delta": guardrail.failure_rate_delta,
                "quarantined_memory_ids": sorted(quarantined),
                "evaluated_at": evaluated_at.isoformat(),
            }
            release.guardrail_report = report
            if pause:
                release.mode = "shadow"
                release.rollout_percent = 0
                release.status = "guardrail_paused"
                release.paused_at = evaluated_at
            if pause or quarantined:
                session.add(
                    AuditEventModel(
                        tenant_id=tenant_id,
                        event_id=str(uuid4()),
                        event_type=(
                            "experience_release.guardrail_paused"
                            if pause
                            else "experience_memory.quarantined"
                        ),
                        actor_subject_id="tenant-experience-worker",
                        resource_type="experience_release",
                        resource_id=release_version,
                        details=report,
                        created_at=evaluated_at,
                    )
                )
            return report

    async def rollback_release(
        self,
        *,
        tenant_id: str,
        release_version: str,
        actor_subject_id: str,
        rolled_back_at: datetime,
    ) -> int:
        async with self._session_factory.begin() as session:
            release = await session.scalar(
                select(ExperienceReleaseModel)
                .where(
                    ExperienceReleaseModel.tenant_id == tenant_id,
                    ExperienceReleaseModel.release_version == release_version,
                )
                .with_for_update()
            )
            if release is None:
                raise LookupError("experience release was not found")
            release_memory_ids = set(
                str(value) for value in (release.manifest or {}).get("included_memory_ids", [])
            )
            memories = list(
                await session.scalars(
                    select(TenantCaseMemoryModel)
                    .where(
                        TenantCaseMemoryModel.tenant_id == tenant_id,
                        TenantCaseMemoryModel.status
                        == ExperienceMemoryStatus.OPERATIONAL_ACTIVE.value,
                        *(
                            (TenantCaseMemoryModel.memory_id.in_(release_memory_ids),)
                            if release_memory_ids
                            else ()
                        ),
                    )
                    .with_for_update()
                )
            )
            for memory in memories:
                memory.status = ExperienceMemoryStatus.SHADOW.value
                memory.updated_at = rolled_back_at
            patterns = list(
                await session.scalars(
                    select(TenantPatternMemoryModel)
                    .where(
                        TenantPatternMemoryModel.tenant_id == tenant_id,
                        TenantPatternMemoryModel.status
                        == ExperienceMemoryStatus.OPERATIONAL_ACTIVE.value,
                        *(
                            (TenantPatternMemoryModel.pattern_id.in_(release_memory_ids),)
                            if release_memory_ids
                            else ()
                        ),
                    )
                    .with_for_update()
                )
            )
            for pattern in patterns:
                pattern.status = ExperienceMemoryStatus.SHADOW.value
                pattern.updated_at = rolled_back_at
            release.mode = "off"
            release.rollout_percent = 0
            release.status = "rolled_back"
            release.rolled_back_at = rolled_back_at
            session.add(
                AuditEventModel(
                    tenant_id=tenant_id,
                    event_id=str(uuid4()),
                    event_type="tenant_experience.release_rolled_back",
                    actor_subject_id=actor_subject_id,
                    resource_type="experience_release",
                    resource_id=release_version,
                    details={
                        "deactivated_memory_count": len(memories),
                        "deactivated_pattern_count": len(patterns),
                    },
                    created_at=rolled_back_at,
                )
            )
            return len(memories) + len(patterns)

    async def delete_conversation_experience(
        self,
        *,
        tenant_id: str,
        conversation_id: str,
        deleted_at: datetime,
    ) -> dict[str, int]:
        async with self._session_factory.begin() as session:
            episode_ids = set(
                await session.scalars(
                    select(IssueOutcomeEpisodeModel.episode_id).where(
                        IssueOutcomeEpisodeModel.tenant_id == tenant_id,
                        IssueOutcomeEpisodeModel.conversation_id == conversation_id,
                    )
                )
            )
            memory_ids = (
                set(
                    await session.scalars(
                        select(TenantCaseMemoryModel.memory_id).where(
                            TenantCaseMemoryModel.tenant_id == tenant_id,
                            TenantCaseMemoryModel.source_episode_id.in_(episode_ids),
                        )
                    )
                )
                if episode_ids
                else set()
            )
            patterns = list(
                await session.scalars(
                    select(TenantPatternMemoryModel)
                    .where(TenantPatternMemoryModel.tenant_id == tenant_id)
                    .with_for_update()
                )
            )
            affected_patterns = 0
            affected_pattern_ids: set[str] = set()
            for pattern in patterns:
                remaining = [
                    value for value in pattern.source_memory_ids or [] if value not in memory_ids
                ]
                if len(remaining) == len(pattern.source_memory_ids or []):
                    continue
                pattern.source_memory_ids = remaining
                pattern.needs_rebuild = True
                pattern.status = ExperienceMemoryStatus.INACTIVE.value
                pattern.updated_at = deleted_at
                affected_patterns += 1
                affected_pattern_ids.add(pattern.pattern_id)
            if affected_pattern_ids:
                candidates = list(
                    await session.scalars(
                        select(ImprovementCandidateModel)
                        .where(ImprovementCandidateModel.tenant_id == tenant_id)
                        .with_for_update()
                    )
                )
                for candidate in candidates:
                    if affected_pattern_ids.intersection(candidate.source_pattern_ids or []):
                        candidate.status = "needs_rebuild"
                        candidate.updated_at = deleted_at
            if memory_ids:
                usage_result = await session.execute(
                    delete(ExperienceMemoryUsageModel).where(
                        ExperienceMemoryUsageModel.tenant_id == tenant_id,
                        or_(
                            ExperienceMemoryUsageModel.conversation_id == conversation_id,
                            ExperienceMemoryUsageModel.memory_id.in_(memory_ids),
                        ),
                    )
                )
                case_result = await session.execute(
                    delete(TenantCaseMemoryModel).where(
                        TenantCaseMemoryModel.tenant_id == tenant_id,
                        TenantCaseMemoryModel.memory_id.in_(memory_ids),
                    )
                )
            else:
                usage_result = await session.execute(
                    delete(ExperienceMemoryUsageModel).where(
                        ExperienceMemoryUsageModel.tenant_id == tenant_id,
                        ExperienceMemoryUsageModel.conversation_id == conversation_id,
                    )
                )
                case_result = None
            episode_result = await session.execute(
                delete(IssueOutcomeEpisodeModel).where(
                    IssueOutcomeEpisodeModel.tenant_id == tenant_id,
                    IssueOutcomeEpisodeModel.conversation_id == conversation_id,
                )
            )
            event_result = await session.execute(
                delete(ExperienceEventModel).where(
                    ExperienceEventModel.tenant_id == tenant_id,
                    ExperienceEventModel.conversation_id == conversation_id,
                )
            )
            summary_result = await session.execute(
                delete(ConversationSummarySegmentModel).where(
                    ConversationSummarySegmentModel.tenant_id == tenant_id,
                    ConversationSummarySegmentModel.conversation_id == conversation_id,
                )
            )
            experiment_result = await session.execute(
                delete(ExperienceExperimentModel).where(
                    ExperienceExperimentModel.tenant_id == tenant_id,
                    ExperienceExperimentModel.conversation_id == conversation_id,
                )
            )
            return {
                "episodes": episode_result.rowcount or 0,
                "case_memories": case_result.rowcount if case_result is not None else 0,
                "usages": usage_result.rowcount or 0,
                "events": event_result.rowcount or 0,
                "summary_segments": summary_result.rowcount or 0,
                "experiments": experiment_result.rowcount or 0,
                "patterns_marked_for_rebuild": affected_patterns,
            }

    async def invalidate_dependencies(
        self,
        *,
        tenant_id: str,
        dependency_kind: str,
        dependency_values: tuple[str, ...],
        invalidated_at: datetime,
    ) -> dict[str, int]:
        allowed = {
            "knowledge_version_ids",
            "policy_version",
            "prompt_version",
            "planner_prompt_version",
            "rule_set_version",
            "model_version",
            "planner_model_version",
            "product_snapshot_id",
            "summary_schema_version",
        }
        if dependency_kind not in allowed:
            raise ValueError("unsupported dependency kind")
        targets = {str(value) for value in dependency_values if str(value)}
        if not targets:
            return {"case_memories": 0, "patterns": 0, "candidates": 0}
        async with self._session_factory.begin() as session:
            cases = list(
                await session.scalars(
                    select(TenantCaseMemoryModel)
                    .where(
                        TenantCaseMemoryModel.tenant_id == tenant_id,
                        TenantCaseMemoryModel.freshness == "current",
                    )
                    .with_for_update()
                )
            )
            stale_ids: set[str] = set()
            for memory in cases:
                if not _dependency_matches(
                    dict(memory.dependencies or {}), dependency_kind, targets
                ):
                    continue
                memory.freshness = "stale"
                memory.status = ExperienceMemoryStatus.STALE.value
                memory.updated_at = invalidated_at
                stale_ids.add(memory.memory_id)
            patterns = list(
                await session.scalars(
                    select(TenantPatternMemoryModel)
                    .where(TenantPatternMemoryModel.tenant_id == tenant_id)
                    .with_for_update()
                )
            )
            affected_pattern_ids: set[str] = set()
            for pattern in patterns:
                if not stale_ids.intersection(pattern.source_memory_ids or []):
                    continue
                pattern.needs_rebuild = True
                pattern.status = ExperienceMemoryStatus.INACTIVE.value
                pattern.updated_at = invalidated_at
                affected_pattern_ids.add(pattern.pattern_id)
            candidate_count = 0
            if affected_pattern_ids:
                candidates = list(
                    await session.scalars(
                        select(ImprovementCandidateModel)
                        .where(ImprovementCandidateModel.tenant_id == tenant_id)
                        .with_for_update()
                    )
                )
                for candidate in candidates:
                    if not affected_pattern_ids.intersection(candidate.source_pattern_ids or []):
                        continue
                    candidate.status = "needs_rebuild"
                    candidate.updated_at = invalidated_at
                    candidate_count += 1
            return {
                "case_memories": len(stale_ids),
                "patterns": len(affected_pattern_ids),
                "candidates": candidate_count,
            }

    async def _query_vector(self, text: str) -> list[float] | None:
        try:
            async with asyncio.timeout(self._retrieval_timeout_seconds):
                values = await self._embedding_provider.embed([text[:2000]])
        except Exception:  # noqa: BLE001
            return None
        if not values or not values[0]:
            return None
        vector = [float(value) for value in values[0]]
        return (
            vector
            if len(vector) == self._embedding_dimension
            else _project_vector(vector, self._embedding_dimension)
        )

    def _validate_eval_metrics(self, metrics: dict[str, object]) -> None:
        required = {
            "metrics_schema_version",
            "dataset_fingerprint",
            "deduplicated_case_count",
            "minimum_segment_samples",
            "issue_segmentation_accuracy",
            "outcome_label_accuracy",
            "baseline_release_version",
            "evaluated_memory_ids",
            "cross_tenant_leakage",
            "stale_memory_usage",
            "unredacted_experience",
            "precision_at_1",
            "negative_transfer_rate",
            "correct_handoff_recall_delta",
            "reopen_rate_delta",
            "human_correction_rate_delta",
            "retrieval_p95_ms",
            "deletion_propagation_rate",
        }
        missing = sorted(required - metrics.keys())
        if missing:
            raise ValueError(f"evaluation metrics missing: {', '.join(missing)}")
        if int(metrics["metrics_schema_version"]) < 2:
            raise ValueError("experience evaluation metrics schema must be version 2 or newer")
        if not re.fullmatch(r"[0-9a-f]{64}", str(metrics["dataset_fingerprint"])):
            raise ValueError(
                "experience evaluation dataset fingerprint must be a SHA-256 hex digest"
            )
        if int(metrics["deduplicated_case_count"]) < 1:
            raise ValueError("experience evaluation requires deduplicated cases")
        if int(metrics["minimum_segment_samples"]) < 1:
            raise ValueError("experience evaluation requires segment sample counts")
        if not str(metrics["baseline_release_version"]).strip():
            raise ValueError("experience evaluation baseline release version is required")
        evaluated_memory_ids = metrics["evaluated_memory_ids"]
        if not isinstance(evaluated_memory_ids, list) or not evaluated_memory_ids:
            raise ValueError("experience evaluation must identify evaluated memories")


def _applicability(payload: dict) -> ExperienceApplicability:
    return ExperienceApplicability(
        intents=tuple(str(value) for value in payload.get("intents", [])),
        product_references=tuple(str(value) for value in payload.get("product_references", [])),
        materials=tuple(str(value) for value in payload.get("materials", [])),
        regions=tuple(str(value) for value in payload.get("regions", [])),
        minimum_risk=int(payload.get("minimum_risk", 0)),
        maximum_risk=int(payload.get("maximum_risk", 1)),
        exclusions=tuple(str(value) for value in payload.get("exclusions", [])),
    )


def _manifest_allows_request(
    manifest: dict[str, object],
    *,
    site_id: str,
    intent: str | None,
    risk_level: int,
) -> bool:
    eligible_sites = {str(value) for value in manifest.get("eligible_sites", [])}
    eligible_intents = {str(value) for value in manifest.get("eligible_intents", [])}
    if eligible_sites and site_id not in eligible_sites:
        return False
    if eligible_intents and intent not in eligible_intents:
        return False
    return risk_level <= int(manifest.get("risk_ceiling", 1))


def _manifest_allows_memory(manifest: dict[str, object], memory_id: str) -> bool:
    included = {str(value) for value in manifest.get("included_memory_ids", [])}
    excluded = {str(value) for value in manifest.get("excluded_memory_ids", [])}
    return memory_id not in excluded and (not included or memory_id in included)


def _normalized_values(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))


def _guidance(
    model: TenantCaseMemoryModel, applicability: ExperienceApplicability
) -> ExperienceGuidance:
    payload = dict(model.guidance or {})
    return ExperienceGuidance(
        memory_id=model.memory_id,
        polarity=ExperiencePolarity(model.polarity),
        summary=model.summary,
        avoid_actions=tuple(str(value) for value in payload.get("avoid_actions", [])),
        suggested_clarifications=tuple(
            str(value) for value in payload.get("suggested_clarifications", [])
        ),
        retrieval_hints=tuple(str(value) for value in payload.get("retrieval_hints", [])),
        escalation_hint=(
            str(payload["escalation_hint"]) if payload.get("escalation_hint") else None
        ),
        approved_evidence_refs=tuple(
            str(value) for value in payload.get("approved_evidence_refs", [])
        ),
        applicability=applicability,
        outcome_evidence_grade=OutcomeEvidenceGrade(model.outcome_evidence_grade),
        freshness=model.freshness,
        sample_count=model.sample_count,
        uncertainty=model.uncertainty,
        wilson_lower_bound=model.wilson_lower_bound,
        limitations=tuple(str(value) for value in payload.get("limitations", [])),
    )


def _pattern_guidance(model: TenantPatternMemoryModel) -> ExperienceGuidance:
    payload = dict(model.guidance or {})
    intent = str(payload.get("intent") or "")
    root_cause = str(payload.get("root_cause") or "outcome_pattern")
    hints = tuple(str(value) for value in payload.get("retrieval_hints", []))
    return ExperienceGuidance(
        memory_id=model.pattern_id,
        polarity=ExperiencePolarity(str(payload.get("polarity") or "neutral")),
        summary=" ".join(
            value
            for value in (
                f"Intent: {intent}" if intent else "",
                f"Pattern: {root_cause}",
                *hints,
            )
            if value
        )[:1200],
        avoid_actions=tuple(str(value) for value in payload.get("avoid_actions", [])),
        suggested_clarifications=tuple(
            str(value) for value in payload.get("suggested_clarifications", [])
        ),
        retrieval_hints=hints,
        escalation_hint=(
            str(payload["escalation_hint"]) if payload.get("escalation_hint") else None
        ),
        applicability=ExperienceApplicability(
            intents=(intent,) if intent else (),
            product_references=tuple(str(value) for value in payload.get("product_references", [])),
            minimum_risk=0,
            maximum_risk=1,
            exclusions=("authoritative_fact_source",),
        ),
        outcome_evidence_grade=OutcomeEvidenceGrade.MODERATE,
        freshness="current",
        sample_count=int(payload.get("case_count") or 0),
        uncertainty=float(payload.get("uncertainty") or 1.0),
        wilson_lower_bound=float(payload.get("wilson_lower_bound") or 0.0),
        limitations=tuple(str(value) for value in payload.get("limitations", [])),
    )


def _guidance_payload(episode: IssueOutcomeEpisodeModel, polarity: ExperiencePolarity) -> dict:
    labels = {str(value) for value in episode.correction_labels or []}
    clarifications = []
    if labels & {"wrong_product_understanding", "missing_product_reference"}:
        clarifications.append("confirm_product_or_material_before_answering")
    if labels & {"missing_context", "conversation_context_lost"}:
        clarifications.append("confirm_the_unresolved_customer_goal")
    avoid_actions = (
        sorted(labels)
        if polarity is ExperiencePolarity.NEGATIVE and labels
        else ["repeat_unverified_ai_attempt"]
        if polarity is ExperiencePolarity.NEGATIVE
        else []
    )
    return {
        "root_cause": _root_cause_from_labels(labels, episode.handoff_reason),
        "avoid_actions": avoid_actions,
        "suggested_clarifications": clarifications,
        "retrieval_hints": [
            episode.intent,
            *[str(value) for value in episode.product_references or []],
        ][:6],
        "escalation_hint": (
            "follow_the_existing_deterministic_handoff_policy"
            if episode.status == ExperienceOutcome.CORRECT_POLICY_HANDOFF.value
            else None
        ),
        "approved_evidence_refs": list(episode.evidence_references or []),
        "limitations": [
            "advisory_only",
            "not_an_authoritative_fact_source",
            "cannot_reduce_deterministic_risk",
        ],
    }


def _root_cause_from_labels(labels: set[str], handoff_reason: str | None) -> str:
    priority = (
        "knowledge_missing",
        "knowledge_stale_or_conflicting",
        "retrieval_miss",
        "wrong_product_understanding",
        "conversation_context_lost",
        "recommendation_mismatch",
        "unsupported_claim",
        "tool_failure",
    )
    return next(
        (value for value in priority if value in labels),
        _failure_label_from_handoff_reason(handoff_reason),
    )


def _aggregate_pattern_guidance(
    memories: list[TenantCaseMemoryModel],
    *,
    intent: str,
    root_cause: str,
) -> dict[str, object]:
    avoid_actions: list[str] = []
    clarifications: list[str] = []
    retrieval_hints: list[str] = [intent]
    limitations: list[str] = [
        "advisory_only",
        "not_an_authoritative_fact_source",
        "cannot_reduce_deterministic_risk",
    ]
    escalation_hints: list[str] = []
    product_references: list[str] = []
    for memory in memories:
        payload = dict(memory.guidance or {})
        avoid_actions.extend(str(value) for value in payload.get("avoid_actions", []))
        clarifications.extend(str(value) for value in payload.get("suggested_clarifications", []))
        retrieval_hints.extend(str(value) for value in payload.get("retrieval_hints", []))
        limitations.extend(str(value) for value in payload.get("limitations", []))
        if payload.get("escalation_hint"):
            escalation_hints.append(str(payload["escalation_hint"]))
        applicability = dict(memory.applicability or {})
        product_references.extend(
            str(value) for value in applicability.get("product_references", [])
        )
    sample_count = sum(max(1, item.sample_count) for item in memories)
    success_count = sum(max(0, item.success_count) for item in memories)
    return {
        "polarity": memories[0].polarity,
        "intent": intent,
        "root_cause": root_cause,
        "case_count": len(memories),
        "distinct_actor_count": len(
            {item.source_actor_cohort_hash for item in memories if item.source_actor_cohort_hash}
        ),
        "product_references": list(dict.fromkeys(product_references))[:20],
        "uncertainty": 1 / math.sqrt(sample_count),
        "wilson_lower_bound": _wilson_lower_bound(success_count, sample_count),
        "avoid_actions": list(dict.fromkeys(avoid_actions))[:8],
        "suggested_clarifications": list(dict.fromkeys(clarifications))[:8],
        "retrieval_hints": list(dict.fromkeys(retrieval_hints))[:8],
        "escalation_hint": escalation_hints[0] if escalation_hints else None,
        "limitations": list(dict.fromkeys(limitations))[:8],
    }


def _one_case_per_actor(
    memories: list[TenantCaseMemoryModel],
) -> list[TenantCaseMemoryModel]:
    selected: dict[str, TenantCaseMemoryModel] = {}
    for memory in memories:
        # Legacy rows cannot prove actor independence and therefore cannot form new patterns.
        key = memory.source_actor_cohort_hash
        if not key:
            continue
        current = selected.get(key)
        if current is None or memory.updated_at > current.updated_at:
            selected[key] = memory
    return list(selected.values())


def _improvement_candidate_type(root_cause: str) -> str:
    mappings = {
        "knowledge_missing": "knowledge_document",
        "knowledge_stale_or_conflicting": "knowledge_source_refresh",
        "retrieval_miss": "retrieval_evaluation",
        "wrong_product_understanding": "intent_evaluation",
        "conversation_context_lost": "intent_evaluation",
        "unsupported_claim": "commitment_regression",
        "correct_policy_handoff": "deterministic_rule_regression",
        "care_sop_missing": "care_sop_draft",
        "tool_failure": "engineering_defect",
        "recommendation_mismatch": "recommendation_evaluation",
    }
    return mappings.get(root_cause, "evaluation_case")


def _case_summary(episode: IssueOutcomeEpisodeModel) -> str:
    parts = [
        f"Intent: {episode.intent}",
        f"Issue: {redact_sensitive_text(episode.issue_summary)[:700]}",
    ]
    if episode.ai_attempt_summary:
        parts.append(f"AI attempt: {redact_sensitive_text(episode.ai_attempt_summary)[:500]}")
    if episode.human_resolution_summary:
        parts.append(
            f"Observed resolution: {redact_sensitive_text(episode.human_resolution_summary)[:700]}"
        )
    parts.append(f"Resolution status: {episode.resolution_status}")
    parts.append(f"Learning outcome: {episode.learning_outcome}")
    parts.append(f"Outcome source: {episode.outcome_source}")
    return "\n".join(parts)[:2000]


def _response_dependencies(response: AgentResponse) -> dict[str, object]:
    return {
        "knowledge_version_ids": list(response.evidence_ids)[:20],
        "prompt_version": str(response.response_brief.get("policy_version") or "response-brief-v1"),
        "planner_prompt_version": str(
            response.turn_plan.get("policy_version") or "turn-planner-v1"
        ),
        "rule_set_version": "deterministic-risk-v1",
        "model_version": response.model_version,
        "planner_model_version": response.planner_model_version,
        "summary_schema_version": 1,
    }


def _merge_dependencies(
    current: dict[str, object], incoming: dict[str, object]
) -> dict[str, object]:
    merged = dict(current)
    merged["knowledge_version_ids"] = list(
        dict.fromkeys(
            (
                *[str(value) for value in current.get("knowledge_version_ids", [])],
                *[str(value) for value in incoming.get("knowledge_version_ids", [])],
            )
        )
    )[:20]
    for key, value in incoming.items():
        if key != "knowledge_version_ids" and value is not None:
            merged[key] = value
    return merged


def _dependency_matches(
    dependencies: dict[str, object],
    dependency_kind: str,
    targets: set[str],
) -> bool:
    value = dependencies.get(dependency_kind)
    if isinstance(value, list):
        return bool(targets.intersection(str(item) for item in value))
    return value is not None and str(value) in targets


def _semantic_score(
    query: str,
    summary: str,
    query_vector: list[float] | None,
    stored_vector,
) -> float:
    if query_vector is not None and stored_vector is not None:
        stored = [float(value) for value in stored_vector]
        denominator = math.sqrt(sum(v * v for v in query_vector)) * math.sqrt(
            sum(v * v for v in stored)
        )
        if denominator:
            return max(
                0.0,
                min(
                    1.0,
                    sum(a * b for a, b in zip(query_vector, stored, strict=True)) / denominator,
                ),
            )
    query_terms = _terms(query)
    summary_terms = _terms(summary)
    if not query_terms or not summary_terms:
        return 0.0
    return len(query_terms & summary_terms) / len(query_terms | summary_terms)


def _project_vector(vector: list[float], target_dimension: int) -> list[float]:
    projected = [0.0] * target_dimension
    for index, value in enumerate(vector):
        digest = sha256(f"experience-projection:{index}".encode()).digest()
        target = int.from_bytes(digest[:4], "big") % target_dimension
        direction = 1.0 if digest[4] % 2 == 0 else -1.0
        projected[target] += value * direction
    norm = math.sqrt(sum(value * value for value in projected))
    if norm == 0:
        return projected
    return [value / norm for value in projected]


def _terms(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", value.casefold()))


def _set_overlap(left: tuple[str, ...], right: tuple[str, ...]) -> float:
    if not left or not right:
        return 0.5 if not right else 0.0
    first = {value.casefold() for value in left}
    second = {value.casefold() for value in right}
    return len(first & second) / len(first | second)


def _recency_score(updated_at: datetime, now: datetime) -> float:
    age_days = max(0.0, (now - updated_at).total_seconds() / 86400)
    return math.exp(-age_days / 90)


def _wilson_lower_bound(successes: int, samples: int, z: float = 1.96) -> float:
    if samples <= 0:
        return 0.0
    proportion = successes / samples
    denominator = 1 + z * z / samples
    centre = proportion + z * z / (2 * samples)
    margin = z * math.sqrt((proportion * (1 - proportion) + z * z / (4 * samples)) / samples)
    return max(0.0, (centre - margin) / denominator)
