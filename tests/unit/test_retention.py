from datetime import UTC, datetime

import pytest

from app.application.dto import (
    ExecuteRetentionCommand,
    PreviewRetentionCommand,
    RetentionPolicy,
)
from app.application.services import RetentionService
from app.domain.models import RetentionCounts


class FakeRetentionStore:
    def __init__(self) -> None:
        self.executions = 0

    async def preview(self, **_kwargs) -> RetentionCounts:  # type: ignore[no-untyped-def]
        return RetentionCounts(admin_sessions=2, audit_events=3)

    async def execute(self, **_kwargs) -> RetentionCounts:  # type: ignore[no-untyped-def]
        self.executions += 1
        return RetentionCounts(admin_sessions=2, audit_events=3)


def _policy() -> RetentionPolicy:
    return RetentionPolicy(
        policy_version="privacy-v1",
        admin_session_days=30,
        expired_memory_grace_days=7,
        support_operation_days=90,
        audit_event_days=365,
    )


@pytest.mark.asyncio
async def test_retention_preview_is_read_only() -> None:
    store = FakeRetentionStore()
    service = RetentionService(store, execution_enabled=False)

    result = await service.preview(
        PreviewRetentionCommand("tenant-a", _policy(), datetime.now(UTC))
    )

    assert result.executed is False
    assert result.counts.total == 5
    assert store.executions == 0


@pytest.mark.asyncio
async def test_retention_execution_requires_switch_and_human_approval() -> None:
    store = FakeRetentionStore()
    command = ExecuteRetentionCommand(
        tenant_id="tenant-a",
        policy=_policy(),
        evaluated_at=datetime.now(UTC),
        run_key="run-2026-07-16",
        approval_reference="PRIVACY-42",
        actor_subject_id="privacy-owner",
        correlation_id="correlation-1",
    )

    with pytest.raises(PermissionError, match="disabled"):
        await RetentionService(store, execution_enabled=False).execute(command)
    with pytest.raises(PermissionError, match="approval"):
        await RetentionService(store, execution_enabled=True).execute(
            ExecuteRetentionCommand(
                tenant_id=command.tenant_id,
                policy=command.policy,
                evaluated_at=command.evaluated_at,
                run_key=command.run_key,
                approval_reference="",
                actor_subject_id=command.actor_subject_id,
                correlation_id=command.correlation_id,
            )
        )

    result = await RetentionService(store, execution_enabled=True).execute(command)

    assert result.executed is True
    assert store.executions == 1
