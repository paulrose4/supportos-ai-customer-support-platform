import json
from datetime import UTC, datetime

from app.application.dto import RetrieveTenantExperienceCommand
from app.application.services import (
    TenantExperienceLearningService,
    TenantExperienceService,
    TurnUnderstandingService,
)
from app.application.services.retrieval_planning import RetrievalPlanningService
from app.domain.models import (
    AuthenticatedPrincipal,
    ExperienceGuidance,
    ExperienceMemoryUsage,
    ExperiencePolarity,
    ExperienceReleasePolicy,
    RetrievalContext,
)
from app.domain.ports import ChatModelRequest, ChatModelResult


class ExperienceStore:
    def __init__(self, guidance: tuple[ExperienceGuidance, ...] = ()) -> None:
        self.guidance = guidance
        self.retrieve_count = 0
        self.usages: list[ExperienceMemoryUsage] = []
        self.calls: list[str] = []
        self.release_policy: ExperienceReleasePolicy | None = None
        self.audit_error: Exception | None = None

    async def retrieve(self, **kwargs) -> tuple[ExperienceGuidance, ...]:  # type: ignore[no-untyped-def]
        self.retrieve_count += 1
        return self.guidance

    async def record_usage(self, usage: ExperienceMemoryUsage) -> None:
        self.usages.append(usage)

    async def record_usages(self, usages: tuple[ExperienceMemoryUsage, ...]) -> None:
        if self.audit_error is not None:
            raise self.audit_error
        self.usages.extend(usages)

    async def get_release_policy(self, **kwargs):  # type: ignore[no-untyped-def]
        return self.release_policy

    async def reconcile_delayed_outcomes(self, **kwargs) -> int:  # type: ignore[no-untyped-def]
        self.calls.append("reconcile")
        return 1

    async def build_pending_case_memories(self, **kwargs) -> int:  # type: ignore[no-untyped-def]
        self.calls.append("cases")
        return 2

    async def consolidate_patterns(self, **kwargs) -> int:  # type: ignore[no-untyped-def]
        self.calls.append("patterns")
        return 3

    async def generate_improvement_candidates(self, **kwargs) -> int:  # type: ignore[no-untyped-def]
        self.calls.append("candidates")
        return 4

    async def evaluate_release_gates(self, **kwargs) -> dict[str, object]:  # type: ignore[no-untyped-def]
        self.calls.append("gates")
        return {"status": "insufficient_samples"}

    async def enforce_online_guardrails(self, **kwargs) -> dict[str, object]:  # type: ignore[no-untyped-def]
        self.calls.append("guardrails")
        return {"status": "not_active", "paused": False}


class PlannerModel:
    def __init__(self) -> None:
        self.requests: list[ChatModelRequest] = []

    async def generate(self, request: ChatModelRequest) -> ChatModelResult:
        self.requests.append(request)
        return ChatModelResult(
            text=json.dumps(
                {
                    "primary_goal": "answer care question",
                    "sub_questions": [
                        {
                            "id": "q1",
                            "question": "How should it be cleaned?",
                            "evidence_need_ids": ["n1"],
                        }
                    ],
                    "evidence_needs": [
                        {
                            "id": "n1",
                            "description": "retrieve approved care guidance",
                            "capability_hint": "site_knowledge_search",
                        }
                    ],
                    "constraints": [],
                    "ambiguities": [],
                }
            ),
            model="planner-test",
        )


def _principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        subject_id="customer-1",
        tenant_id="tenant-a",
        roles=frozenset({"customer"}),
        scopes=frozenset({"knowledge:read"}),
        authentication_method="test",
        authenticated_at=datetime.now(UTC),
        correlation_id="correlation-1",
        site_id="site-a",
    )


def _guidance(memory_id: str, polarity: ExperiencePolarity) -> ExperienceGuidance:
    return ExperienceGuidance(
        memory_id=memory_id,
        polarity=polarity,
        summary="A redacted historical outcome",
        avoid_actions=("repeat_unverified_ai_attempt",),
        suggested_clarifications=("confirm_material",),
        retrieval_hints=("approved material care SOP",),
        limitations=("not_an_authoritative_fact_source",),
    )


def _command(risk_level: int = 0) -> RetrieveTenantExperienceCommand:
    return RetrieveTenantExperienceCommand(
        principal=_principal(),
        conversation_id="conversation-1",
        trace_id="trace-1",
        message="How should I clean it?",
        intent="product_care",
        product_references=(),
        risk_level=risk_level,
    )


async def test_active_experience_uses_contrastive_cases_and_audits_actual_use() -> None:
    positive = _guidance("positive-1", ExperiencePolarity.POSITIVE)
    negative = _guidance("negative-1", ExperiencePolarity.NEGATIVE)
    store = ExperienceStore((positive, negative))
    service = TenantExperienceService(
        store,
        enabled=True,
        release_mode="active",
        release_version="release-1",
        rollout_percent=100,
    )

    result = await service.retrieve(_command())

    assert [item.memory_id for item in result.guidance] == ["negative-1", "positive-1"]
    assert result.applied is True
    assert result.treatment == "treatment"
    assert {usage.used_for for usage in store.usages} == {("planning", "retrieval_guidance")}
    assert all(usage.selected and usage.influenced for usage in store.usages)


async def test_shadow_experience_is_retrieved_but_never_enters_planning() -> None:
    store = ExperienceStore((_guidance("negative-1", ExperiencePolarity.NEGATIVE),))
    service = TenantExperienceService(
        store,
        enabled=True,
        release_mode="shadow",
        release_version="release-1",
        rollout_percent=0,
    )

    result = await service.retrieve(_command())

    assert result.guidance == ()
    assert result.retrieved_ids == ("negative-1",)
    assert result.applied is False
    assert store.usages[0].used_for == ()


async def test_high_risk_turn_never_queries_tenant_experience() -> None:
    store = ExperienceStore((_guidance("negative-1", ExperiencePolarity.NEGATIVE),))
    service = TenantExperienceService(
        store,
        enabled=True,
        release_mode="active",
        release_version="release-1",
        rollout_percent=100,
        maximum_online_risk=1,
    )

    result = await service.retrieve(_command(risk_level=2))

    assert result.guidance == ()
    assert result.warnings == ("high_risk_excluded",)
    assert store.retrieve_count == 0


async def test_approved_database_release_overrides_shadow_environment_mode() -> None:
    store = ExperienceStore((_guidance("negative-1", ExperiencePolarity.NEGATIVE),))
    store.release_policy = ExperienceReleasePolicy(
        release_version="release-1",
        mode="active",
        rollout_percent=100,
        status="active",
    )
    service = TenantExperienceService(
        store,
        enabled=True,
        release_mode="shadow",
        release_version="release-1",
        rollout_percent=0,
    )

    result = await service.retrieve(_command())

    assert result.applied is True
    assert result.treatment == "treatment"


async def test_release_manifest_excludes_unapproved_intent_before_retrieval() -> None:
    store = ExperienceStore((_guidance("negative-1", ExperiencePolarity.NEGATIVE),))
    store.release_policy = ExperienceReleasePolicy(
        release_version="release-1",
        mode="active",
        rollout_percent=100,
        status="active",
        eligible_intents=("shipping_delay",),
        eligible_sites=("site-a",),
        risk_ceiling=1,
    )
    service = TenantExperienceService(
        store,
        enabled=True,
        release_mode="shadow",
        release_version="release-1",
        rollout_percent=0,
    )

    result = await service.retrieve(_command())

    assert result.applied is False
    assert result.warnings == ("intent_not_in_release",)
    assert store.retrieve_count == 0


async def test_experience_is_not_applied_when_usage_audit_fails() -> None:
    store = ExperienceStore((_guidance("negative-1", ExperiencePolarity.NEGATIVE),))
    store.audit_error = RuntimeError("database unavailable")
    service = TenantExperienceService(
        store,
        enabled=True,
        release_mode="active",
        release_version="release-1",
        rollout_percent=100,
    )

    result = await service.retrieve(_command())

    assert result.guidance == ()
    assert result.applied is False
    assert result.warnings == ("experience_usage_audit_failed",)


async def test_learning_cycle_runs_result_production_before_release_gates() -> None:
    store = ExperienceStore()
    result = await TenantExperienceLearningService(store).run_cycle(
        tenant_id="tenant-a",
        release_version="release-1",
        batch_size=100,
        evaluated_at=datetime(2026, 7, 31, tzinfo=UTC),
    )

    assert store.calls == [
        "reconcile",
        "guardrails",
        "cases",
        "patterns",
        "candidates",
        "gates",
    ]
    assert result.reconciled_outcomes == 1
    assert result.case_memories_built == 2
    assert result.patterns_consolidated == 3
    assert result.improvement_candidates_created == 4
    assert result.guardrail_report["paused"] is False
    assert result.gate_report["status"] == "insufficient_samples"


async def test_turn_planner_receives_experience_as_non_factual_advisory_input() -> None:
    model = PlannerModel()
    guidance = _guidance("negative-1", ExperiencePolarity.NEGATIVE)

    result = await TurnUnderstandingService(model).understand(
        message="How should I clean it?",
        experience_guidance=(guidance,),
    )

    assert "Avoid: repeat_unverified_ai_attempt" in result.plan.constraints
    assert "confirm_material" in result.plan.ambiguities
    payload = json.loads(model.requests[0].messages[-1]["content"])
    assert payload["tenant_experience_guidance"][0]["memory_id"] == "negative-1"
    assert "summary" not in payload["tenant_experience_guidance"][0]


def test_retrieval_planner_uses_only_structured_experience_hints() -> None:
    guidance = _guidance("negative-1", ExperiencePolarity.NEGATIVE)

    plan = RetrievalPlanningService().plan(
        question="How should I clean it?",
        context=RetrievalContext(),
        base_filters={},
        experience_guidance=(guidance,),
    )

    assert "approved material care SOP" in plan.query_variants
    assert "A redacted historical outcome" not in plan.query_variants
