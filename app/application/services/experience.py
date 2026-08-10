from datetime import UTC, datetime
from hashlib import sha256
from uuid import NAMESPACE_URL, uuid5

from app.application.dto.experience import (
    ExperienceLearningCycleResult,
    RetrieveTenantExperienceCommand,
    TenantExperienceResult,
)
from app.domain.models import ExperienceMemoryUsage
from app.domain.ports import TenantExperiencePort
from app.domain.rules import select_contrastive_experience


class TenantExperienceService:
    def __init__(
        self,
        store: TenantExperiencePort,
        *,
        enabled: bool,
        release_mode: str,
        release_version: str,
        rollout_percent: int,
        maximum_online_risk: int = 1,
        result_limit: int = 2,
    ) -> None:
        if release_mode not in {"off", "shadow", "active"}:
            raise ValueError("experience release mode must be off, shadow, or active")
        if not 0 <= rollout_percent <= 100:
            raise ValueError("experience rollout percent must be between 0 and 100")
        if not 1 <= result_limit <= 4:
            raise ValueError("experience result limit must be between 1 and 4")
        self._store = store
        self._enabled = enabled
        self._release_mode = release_mode
        self._release_version = release_version
        self._rollout_percent = rollout_percent
        self._maximum_online_risk = maximum_online_risk
        self._result_limit = result_limit

    async def retrieve(self, command: RetrieveTenantExperienceCommand) -> TenantExperienceResult:
        if not self._enabled:
            return self._empty("disabled")
        try:
            policy = await self._store.get_release_policy(
                tenant_id=command.principal.tenant_id,
                release_version=self._release_version,
            )
        except Exception:  # noqa: BLE001
            return self._empty("experience_release_lookup_failed")
        release_mode = policy.mode if policy is not None else self._release_mode
        rollout_percent = policy.rollout_percent if policy is not None else self._rollout_percent
        if policy is not None and policy.status in {"rolled_back", "rejected"}:
            release_mode = "off"
        elif policy is not None and release_mode == "active" and policy.status != "active":
            release_mode = "shadow"
        if release_mode == "off":
            return self._empty("disabled")
        if command.risk_level > self._maximum_online_risk:
            return self._empty("high_risk_excluded")
        site_id = command.principal.site_id
        if site_id is None:
            return self._empty("site_identity_required")
        if policy is not None:
            if policy.eligible_sites and site_id not in policy.eligible_sites:
                return self._empty("site_not_in_release")
            if policy.eligible_intents and command.intent not in policy.eligible_intents:
                return self._empty("intent_not_in_release")
            if command.risk_level > policy.risk_ceiling:
                return self._empty("release_risk_excluded")
        try:
            candidates = await self._store.retrieve(
                tenant_id=command.principal.tenant_id,
                site_id=site_id,
                query=command.message,
                intent=command.intent,
                product_references=command.product_references,
                risk_level=command.risk_level,
                release_version=self._release_version,
                include_shadow=release_mode == "shadow",
                limit=self._result_limit,
            )
        except Exception:  # noqa: BLE001
            return self._empty("experience_retrieval_failed")

        selected = select_contrastive_experience(candidates, self._result_limit)
        treatment = self._treatment(
            command,
            release_mode=release_mode,
            rollout_percent=rollout_percent,
        )
        applied = release_mode == "active" and treatment == "treatment"
        used_for = ("planning", "retrieval_guidance") if applied else ()
        now = datetime.now(UTC)
        usages = tuple(
            ExperienceMemoryUsage(
                usage_id=str(
                    uuid5(
                        NAMESPACE_URL,
                        f"{command.principal.tenant_id}:{command.trace_id}:{guidance.memory_id}",
                    )
                ),
                tenant_id=command.principal.tenant_id,
                conversation_id=command.conversation_id,
                trace_id=command.trace_id,
                memory_id=guidance.memory_id,
                release_version=self._release_version,
                treatment=treatment,
                retrieved=True,
                used_for=used_for,
                occurred_at=now,
                eligible=True,
                selected=True,
                influenced=applied,
            )
            for guidance in selected
        )
        try:
            await self._store.record_usages(usages)
        except Exception:  # noqa: BLE001
            return self._empty("experience_usage_audit_failed")
        return TenantExperienceResult(
            guidance=selected if applied else (),
            retrieved_ids=tuple(item.memory_id for item in selected),
            treatment=treatment,
            release_version=self._release_version,
            applied=applied,
            warnings=(() if selected else ("no_applicable_tenant_experience",)),
        )

    def _treatment(
        self,
        command: RetrieveTenantExperienceCommand,
        *,
        release_mode: str,
        rollout_percent: int,
    ) -> str:
        if release_mode == "shadow":
            return "shadow"
        digest = sha256(
            (
                f"{command.principal.tenant_id}:{command.conversation_id}:{self._release_version}"
            ).encode()
        ).digest()
        bucket = int.from_bytes(digest[:4], "big") % 100
        return "treatment" if bucket < rollout_percent else "control"

    def _empty(self, warning: str) -> TenantExperienceResult:
        return TenantExperienceResult(
            guidance=(),
            retrieved_ids=(),
            treatment="off",
            release_version=self._release_version,
            applied=False,
            warnings=(warning,),
        )


class TenantExperienceLearningService:
    def __init__(self, store: TenantExperiencePort) -> None:
        self._store = store

    async def run_cycle(
        self,
        *,
        tenant_id: str,
        release_version: str,
        batch_size: int,
        minimum_cluster_size: int = 3,
        minimum_eval_samples: int = 30,
        guardrail_minimum_samples: int = 30,
        maximum_negative_transfer_rate: float = 0.10,
        maximum_failure_rate_delta: float = 0.05,
        evaluated_at: datetime | None = None,
    ) -> ExperienceLearningCycleResult:
        if not tenant_id.strip():
            raise ValueError("tenant_id is required")
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        now = evaluated_at or datetime.now(UTC)
        reconciled = await self._store.reconcile_delayed_outcomes(
            tenant_id=tenant_id,
            evaluated_at=now,
            limit=batch_size,
        )
        guardrail_report = await self._store.enforce_online_guardrails(
            tenant_id=tenant_id,
            release_version=release_version,
            evaluated_at=now,
            minimum_samples=guardrail_minimum_samples,
            maximum_negative_transfer_rate=maximum_negative_transfer_rate,
            maximum_failure_rate_delta=maximum_failure_rate_delta,
        )
        built = await self._store.build_pending_case_memories(
            tenant_id=tenant_id,
            evaluated_at=now,
            limit=batch_size,
        )
        patterns = await self._store.consolidate_patterns(
            tenant_id=tenant_id,
            evaluated_at=now,
            minimum_cluster_size=minimum_cluster_size,
            limit=batch_size,
        )
        candidates = await self._store.generate_improvement_candidates(
            tenant_id=tenant_id,
            evaluated_at=now,
            limit=batch_size,
        )
        gate_report = await self._store.evaluate_release_gates(
            tenant_id=tenant_id,
            release_version=release_version,
            evaluated_at=now,
            minimum_samples=minimum_eval_samples,
        )
        return ExperienceLearningCycleResult(
            tenant_id=tenant_id,
            release_version=release_version,
            reconciled_outcomes=reconciled,
            guardrail_report=guardrail_report,
            case_memories_built=built,
            patterns_consolidated=patterns,
            improvement_candidates_created=candidates,
            gate_report=gate_report,
            completed_at=now,
        )

    async def activate_release(
        self,
        *,
        tenant_id: str,
        release_version: str,
        approved_by: str,
        rollout_percent: int,
        included_memory_ids: tuple[str, ...] = (),
        excluded_memory_ids: tuple[str, ...] = (),
        eligible_intents: tuple[str, ...] = (),
        eligible_sites: tuple[str, ...] = (),
        risk_ceiling: int = 1,
        dataset_version: str = "",
        activated_at: datetime | None = None,
    ) -> int:
        return await self._store.activate_release(
            tenant_id=tenant_id,
            release_version=release_version,
            approved_by=approved_by,
            rollout_percent=rollout_percent,
            included_memory_ids=included_memory_ids,
            excluded_memory_ids=excluded_memory_ids,
            eligible_intents=eligible_intents,
            eligible_sites=eligible_sites,
            risk_ceiling=risk_ceiling,
            dataset_version=dataset_version,
            activated_at=activated_at or datetime.now(UTC),
        )

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
        created_at: datetime | None = None,
    ) -> None:
        await self._store.record_eval_run(
            tenant_id=tenant_id,
            eval_run_id=eval_run_id,
            release_version=release_version,
            evaluation_mode=evaluation_mode,
            status=status,
            metrics=metrics,
            sample_count=sample_count,
            dataset_version=dataset_version,
            created_at=created_at or datetime.now(UTC),
        )

    async def rollback_release(
        self,
        *,
        tenant_id: str,
        release_version: str,
        actor_subject_id: str,
        rolled_back_at: datetime | None = None,
    ) -> int:
        return await self._store.rollback_release(
            tenant_id=tenant_id,
            release_version=release_version,
            actor_subject_id=actor_subject_id,
            rolled_back_at=rolled_back_at or datetime.now(UTC),
        )

    async def delete_conversation_experience(
        self,
        *,
        tenant_id: str,
        conversation_id: str,
        deleted_at: datetime | None = None,
    ) -> dict[str, int]:
        return await self._store.delete_conversation_experience(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            deleted_at=deleted_at or datetime.now(UTC),
        )

    async def invalidate_dependencies(
        self,
        *,
        tenant_id: str,
        dependency_kind: str,
        dependency_values: tuple[str, ...],
        invalidated_at: datetime | None = None,
    ) -> dict[str, int]:
        return await self._store.invalidate_dependencies(
            tenant_id=tenant_id,
            dependency_kind=dependency_kind,
            dependency_values=dependency_values,
            invalidated_at=invalidated_at or datetime.now(UTC),
        )
