from datetime import datetime, timedelta

from app.application.dto.retention import (
    ExecuteRetentionCommand,
    PreviewRetentionCommand,
    RetentionPolicy,
    RetentionResult,
)
from app.domain.ports.retention import RetentionStorePort


class RetentionService:
    def __init__(self, store: RetentionStorePort, *, execution_enabled: bool) -> None:
        self._store = store
        self._execution_enabled = execution_enabled

    async def preview(self, command: PreviewRetentionCommand) -> RetentionResult:
        _validate_tenant(command.tenant_id)
        _validate_policy(command.policy)
        cutoffs = _cutoffs(command.policy, command.evaluated_at)
        counts = await self._store.preview(tenant_id=command.tenant_id, **cutoffs)
        return RetentionResult(
            tenant_id=command.tenant_id,
            policy_version=command.policy.policy_version,
            counts=counts,
            executed=False,
        )

    async def execute(self, command: ExecuteRetentionCommand) -> RetentionResult:
        if not self._execution_enabled:
            raise PermissionError("retention execution is disabled")
        _validate_tenant(command.tenant_id)
        _validate_policy(command.policy)
        if not command.run_key.strip():
            raise ValueError("retention run_key is required")
        if not command.approval_reference.strip():
            raise PermissionError("human approval reference is required")
        if not command.actor_subject_id.strip():
            raise PermissionError("trusted operator identity is required")
        cutoffs = _cutoffs(command.policy, command.evaluated_at)
        counts = await self._store.execute(
            tenant_id=command.tenant_id,
            run_key=command.run_key.strip(),
            policy_version=command.policy.policy_version,
            approval_reference=command.approval_reference.strip(),
            actor_subject_id=command.actor_subject_id.strip(),
            correlation_id=command.correlation_id,
            executed_at=command.evaluated_at,
            **cutoffs,
        )
        return RetentionResult(
            tenant_id=command.tenant_id,
            policy_version=command.policy.policy_version,
            counts=counts,
            executed=True,
        )


def _validate_tenant(tenant_id: str) -> None:
    if not tenant_id.strip():
        raise ValueError("tenant_id is required")


def _validate_policy(policy: RetentionPolicy) -> None:
    if not policy.policy_version.strip():
        raise ValueError("retention policy version is required")
    values = (
        policy.admin_session_days,
        policy.expired_memory_grace_days,
        policy.support_operation_days,
        policy.audit_event_days,
    )
    if any(value < 1 for value in values):
        raise ValueError("retention durations must be at least one day")


def _cutoffs(policy: RetentionPolicy, evaluated_at: datetime) -> dict[str, datetime]:
    return {
        "session_cutoff": evaluated_at - timedelta(days=policy.admin_session_days),
        "memory_cutoff": evaluated_at - timedelta(days=policy.expired_memory_grace_days),
        "operation_cutoff": evaluated_at - timedelta(days=policy.support_operation_days),
        "audit_cutoff": evaluated_at - timedelta(days=policy.audit_event_days),
    }
