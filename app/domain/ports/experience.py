from datetime import datetime
from typing import Protocol

from app.domain.models.experience import (
    ExperienceGuidance,
    ExperienceMemoryUsage,
    ExperienceReleasePolicy,
)


class TenantExperiencePort(Protocol):
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
    ) -> tuple[ExperienceGuidance, ...]: ...

    async def record_usage(self, usage: ExperienceMemoryUsage) -> None: ...

    async def record_usages(self, usages: tuple[ExperienceMemoryUsage, ...]) -> None: ...

    async def get_release_policy(
        self,
        *,
        tenant_id: str,
        release_version: str,
    ) -> ExperienceReleasePolicy | None: ...

    async def build_pending_case_memories(
        self,
        *,
        tenant_id: str,
        evaluated_at: datetime,
        limit: int,
    ) -> int: ...

    async def reconcile_delayed_outcomes(
        self,
        *,
        tenant_id: str,
        evaluated_at: datetime,
        limit: int,
    ) -> int: ...

    async def consolidate_patterns(
        self,
        *,
        tenant_id: str,
        evaluated_at: datetime,
        minimum_cluster_size: int,
        limit: int,
    ) -> int: ...

    async def generate_improvement_candidates(
        self,
        *,
        tenant_id: str,
        evaluated_at: datetime,
        limit: int,
    ) -> int: ...

    async def evaluate_release_gates(
        self,
        *,
        tenant_id: str,
        release_version: str,
        evaluated_at: datetime,
        minimum_samples: int,
    ) -> dict[str, object]: ...

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
    ) -> None: ...

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
    ) -> int: ...

    async def enforce_online_guardrails(
        self,
        *,
        tenant_id: str,
        release_version: str,
        evaluated_at: datetime,
        minimum_samples: int,
        maximum_negative_transfer_rate: float,
        maximum_failure_rate_delta: float,
    ) -> dict[str, object]: ...

    async def rollback_release(
        self,
        *,
        tenant_id: str,
        release_version: str,
        actor_subject_id: str,
        rolled_back_at: datetime,
    ) -> int: ...

    async def delete_conversation_experience(
        self,
        *,
        tenant_id: str,
        conversation_id: str,
        deleted_at: datetime,
    ) -> dict[str, int]: ...

    async def invalidate_dependencies(
        self,
        *,
        tenant_id: str,
        dependency_kind: str,
        dependency_values: tuple[str, ...],
        invalidated_at: datetime,
    ) -> dict[str, int]: ...
