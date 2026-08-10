import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.application.tenant_context import tenant_scope
from app.domain.models import (
    AgentResponse,
    AuthenticatedPrincipal,
    ExperiencePolarity,
    ResponseKind,
    RiskLevel,
)
from app.integrations.llm import FakeEmbeddingProvider
from app.integrations.postgres import (
    DatabaseSessionManager,
    PostgreSQLConversationPersistenceAdapter,
    PostgreSQLTenantExperienceAdapter,
)
from app.integrations.postgres.experience import (
    record_human_message_experience,
    record_manual_resolution_experience,
    record_satisfaction_experience,
)
from app.integrations.postgres.models import (
    AgentRunModel,
    AuditEventModel,
    ConversationModel,
    ExperienceEvalRunModel,
    ExperienceEventModel,
    ExperienceExperimentModel,
    ExperienceMemoryUsageModel,
    ExperienceReleaseModel,
    IssueOutcomeEpisodeModel,
    MessageModel,
    TenantCaseMemoryModel,
    TenantPatternMemoryModel,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION_TESTS") != "1",
        reason="set RUN_INTEGRATION_TESTS=1 with PostgreSQL running",
    ),
]

DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/customer_agent",
)


def _principal(tenant_id: str) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        subject_id="anonymous-visitor",
        tenant_id=tenant_id,
        roles=frozenset({"customer"}),
        scopes=frozenset({"knowledge:read"}),
        authentication_method="public_widget_token",
        authenticated_at=datetime.now(UTC),
        correlation_id=str(uuid4()),
        site_id="site-a",
    )


async def test_postgres_experience_builds_redacted_negative_case_and_propagates_delete() -> None:
    tenant_id = f"experience-test-{uuid4()}"
    conversation_id = str(uuid4())
    trace_id = str(uuid4())
    manager = DatabaseSessionManager(DATABASE_URL)
    conversations = PostgreSQLConversationPersistenceAdapter(manager.session_factory)
    experience = PostgreSQLTenantExperienceAdapter(
        manager.session_factory,
        FakeEmbeddingProvider(),
        minimum_similarity=0.0,
    )
    principal = _principal(tenant_id)
    response = AgentResponse(
        conversation_id=conversation_id,
        message="I need a human to verify the missing care instructions.",
        kind=ResponseKind.HANDOFF,
        risk_level=RiskLevel.PUBLIC,
        trace_id=trace_id,
        handoff_id=str(uuid4()),
        handoff_reason="knowledge_insufficient",
    )

    try:
        with tenant_scope(tenant_id):
            await conversations.persist_exchange(
                principal=principal,
                user_message="How should I clean SKU-100? Contact me at person@example.com",
                response=response,
            )
            now = datetime.now(UTC)
            async with manager.session_factory.begin() as session:
                await record_human_message_experience(
                    session,
                    tenant_id=tenant_id,
                    site_id="site-a",
                    conversation_id=conversation_id,
                    message_id=str(uuid4()),
                    content="Use the approved product-specific care SOP.",
                    actor_subject_id="agent-1",
                    occurred_at=now,
                )
                await record_manual_resolution_experience(
                    session,
                    tenant_id=tenant_id,
                    site_id="site-a",
                    conversation_id=conversation_id,
                    actor_subject_id="agent-1",
                    occurred_at=now,
                )

            assert (
                await experience.build_pending_case_memories(
                    tenant_id=tenant_id,
                    evaluated_at=now,
                    limit=10,
                )
                == 1
            )
            async with manager.session_factory() as session:
                episode = await session.scalar(
                    select(IssueOutcomeEpisodeModel).where(
                        IssueOutcomeEpisodeModel.tenant_id == tenant_id
                    )
                )
                memory = await session.scalar(
                    select(TenantCaseMemoryModel).where(
                        TenantCaseMemoryModel.tenant_id == tenant_id
                    )
                )
            assert episode is not None and episode.status == "avoidable_handoff"
            assert "knowledge_missing" in episode.correction_labels
            assert memory is not None and memory.polarity == ExperiencePolarity.NEGATIVE.value
            assert "person@example.com" not in memory.summary
            assert len(memory.embedding) == 384

            retrieved = await experience.retrieve(
                tenant_id=tenant_id,
                site_id="site-a",
                query="clean SKU-100",
                intent="product_care",
                product_references=("SKU-100",),
                risk_level=0,
                release_version="experience-v1",
                include_shadow=True,
                limit=2,
            )
            assert [item.memory_id for item in retrieved] == [memory.memory_id]
            assert (
                await experience.retrieve(
                    tenant_id=f"other-{tenant_id}",
                    site_id="site-a",
                    query="clean SKU-100",
                    intent="product_care",
                    product_references=("SKU-100",),
                    risk_level=0,
                    release_version="experience-v1",
                    include_shadow=True,
                    limit=2,
                )
                == ()
            )

            revised_at = now + timedelta(seconds=1)
            async with manager.session_factory.begin() as session:
                await record_satisfaction_experience(
                    session,
                    tenant_id=tenant_id,
                    site_id="site-a",
                    conversation_id=conversation_id,
                    rating_id=str(uuid4()),
                    score=5,
                    occurred_at=revised_at,
                )
            assert (
                await experience.build_pending_case_memories(
                    tenant_id=tenant_id,
                    evaluated_at=revised_at + timedelta(seconds=1),
                    limit=10,
                )
                == 1
            )
            async with manager.session_factory() as session:
                rebuilt = await session.scalar(
                    select(TenantCaseMemoryModel).where(
                        TenantCaseMemoryModel.tenant_id == tenant_id
                    )
                )
                revised_episode = await session.scalar(
                    select(IssueOutcomeEpisodeModel).where(
                        IssueOutcomeEpisodeModel.tenant_id == tenant_id
                    )
                )
            assert rebuilt is not None
            # A positive CSAT proves the customer was satisfied, but it must not erase
            # the stronger human finding that the AI handoff was avoidable.
            assert rebuilt.polarity == ExperiencePolarity.NEGATIVE.value
            assert rebuilt.status == "shadow"
            assert revised_episode is not None
            assert revised_episode.resolution_status == "resolved"
            assert revised_episode.learning_outcome == "avoidable_handoff"
            assert revised_episode.outcome_source == "human_verified"
            assert revised_episode.outcome_conflict_status == "positive_overridden"

            deleted = await experience.delete_conversation_experience(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                deleted_at=datetime.now(UTC),
            )
            assert deleted["episodes"] == 1
            assert deleted["case_memories"] == 1
            assert deleted["events"] >= 3
    finally:
        with tenant_scope(tenant_id):
            async with manager.session_factory.begin() as session:
                for model in (
                    ExperienceMemoryUsageModel,
                    ExperienceExperimentModel,
                    TenantPatternMemoryModel,
                    TenantCaseMemoryModel,
                    ExperienceEventModel,
                    IssueOutcomeEpisodeModel,
                    MessageModel,
                    AgentRunModel,
                    ConversationModel,
                    AuditEventModel,
                ):
                    await session.execute(delete(model).where(model.tenant_id == tenant_id))
        await manager.dispose()


async def test_release_manifest_and_online_guardrails_limit_blast_radius() -> None:
    tenant_id = f"experience-release-test-{uuid4()}"
    memory_id = str(uuid4())
    release_version = "experience-canary-v1"
    now = datetime.now(UTC)
    manager = DatabaseSessionManager(DATABASE_URL)
    experience = PostgreSQLTenantExperienceAdapter(
        manager.session_factory,
        FakeEmbeddingProvider(),
        minimum_similarity=0.0,
    )
    try:
        with tenant_scope(tenant_id):
            async with manager.session_factory.begin() as session:
                session.add(
                    TenantCaseMemoryModel(
                        tenant_id=tenant_id,
                        memory_id=memory_id,
                        site_id="site-a",
                        source_episode_id=str(uuid4()),
                        source_actor_cohort_hash="actor-a",
                        issue_signature="a" * 64,
                        polarity="negative",
                        summary="Avoid repeating an unsupported answer.",
                        guidance={"avoid_actions": ["repeat_unverified_ai_attempt"]},
                        applicability={
                            "intents": ["product_care"],
                            "minimum_risk": 0,
                            "maximum_risk": 1,
                        },
                        dependencies={},
                        outcome_evidence_grade="strong",
                        freshness="current",
                        status="shadow",
                        sample_count=1,
                        success_count=0,
                        failure_count=1,
                        negative_transfer_count=0,
                        uncertainty=1.0,
                        wilson_lower_bound=0.0,
                        embedding=None,
                        created_at=now,
                        updated_at=now,
                    )
                )
            await experience.record_eval_run(
                tenant_id=tenant_id,
                eval_run_id=str(uuid4()),
                release_version=release_version,
                evaluation_mode="replay",
                status="completed",
                metrics={
                    "metrics_schema_version": 2,
                    "dataset_fingerprint": "a" * 64,
                    "deduplicated_case_count": 30,
                    "minimum_segment_samples": 10,
                    "issue_segmentation_accuracy": 0.97,
                    "outcome_label_accuracy": 0.97,
                    "baseline_release_version": "experience-v0",
                    "evaluated_memory_ids": [memory_id],
                    "cross_tenant_leakage": 0,
                    "stale_memory_usage": 0,
                    "unredacted_experience": 0,
                    "precision_at_1": 0.85,
                    "negative_transfer_rate": 0.0,
                    "correct_handoff_recall_delta": 0.0,
                    "reopen_rate_delta": 0.0,
                    "human_correction_rate_delta": 0.0,
                    "retrieval_p95_ms": 100.0,
                    "deletion_propagation_rate": 1.0,
                },
                sample_count=30,
                dataset_version="replay-v1",
                created_at=now,
            )
            gate_report = await experience.evaluate_release_gates(
                tenant_id=tenant_id,
                release_version=release_version,
                evaluated_at=now,
                minimum_samples=30,
            )
            assert gate_report["status"] == "passed"

            activated = await experience.activate_release(
                tenant_id=tenant_id,
                release_version=release_version,
                approved_by="operator-1",
                rollout_percent=50,
                included_memory_ids=(memory_id,),
                excluded_memory_ids=(),
                eligible_intents=("product_care",),
                eligible_sites=("site-a",),
                risk_ceiling=1,
                dataset_version="replay-v1",
                activated_at=now,
            )
            assert activated == 1
            policy = await experience.get_release_policy(
                tenant_id=tenant_id,
                release_version=release_version,
            )
            assert policy is not None
            assert policy.included_memory_ids == (memory_id,)
            assert policy.eligible_intents == ("product_care",)
            assert (
                await experience.retrieve(
                    tenant_id=tenant_id,
                    site_id="site-b",
                    query="clean it",
                    intent="product_care",
                    product_references=(),
                    risk_level=0,
                    release_version=release_version,
                    include_shadow=False,
                    limit=2,
                )
                == ()
            )

            async with manager.session_factory.begin() as session:
                for index in range(10):
                    session.add(
                        ExperienceMemoryUsageModel(
                            tenant_id=tenant_id,
                            usage_id=f"treatment-{index}",
                            conversation_id=f"treatment-conversation-{index}",
                            trace_id=f"treatment-trace-{index}",
                            memory_id=memory_id,
                            release_version=release_version,
                            treatment="treatment",
                            retrieved=True,
                            eligible=True,
                            selected=True,
                            influenced=True,
                            outcome_attributed=True,
                            used_for=["planning"],
                            outcome="failed_answer",
                            outcome_observed_at=now,
                            occurred_at=now,
                        )
                    )
                    session.add(
                        ExperienceMemoryUsageModel(
                            tenant_id=tenant_id,
                            usage_id=f"control-{index}",
                            conversation_id=f"control-conversation-{index}",
                            trace_id=f"control-trace-{index}",
                            memory_id=memory_id,
                            release_version=release_version,
                            treatment="control",
                            retrieved=True,
                            eligible=True,
                            selected=True,
                            influenced=False,
                            outcome_attributed=True,
                            used_for=[],
                            outcome="successful_self_service",
                            outcome_observed_at=now,
                            occurred_at=now,
                        )
                    )

            report = await experience.enforce_online_guardrails(
                tenant_id=tenant_id,
                release_version=release_version,
                evaluated_at=now + timedelta(minutes=1),
                minimum_samples=10,
                maximum_negative_transfer_rate=0.25,
                maximum_failure_rate_delta=0.05,
            )
            assert report["paused"] is True
            assert report["quarantined_memory_ids"] == [memory_id]
            policy = await experience.get_release_policy(
                tenant_id=tenant_id,
                release_version=release_version,
            )
            assert policy is not None and policy.status == "guardrail_paused"
    finally:
        with tenant_scope(tenant_id):
            async with manager.session_factory.begin() as session:
                for model in (
                    ExperienceMemoryUsageModel,
                    ExperienceExperimentModel,
                    TenantPatternMemoryModel,
                    TenantCaseMemoryModel,
                    ExperienceEvalRunModel,
                    ExperienceReleaseModel,
                    ExperienceEventModel,
                    IssueOutcomeEpisodeModel,
                    AuditEventModel,
                ):
                    await session.execute(delete(model).where(model.tenant_id == tenant_id))
        await manager.dispose()
