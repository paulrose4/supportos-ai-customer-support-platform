from datetime import UTC, datetime

import pytest

from app.application.dto import RunAutoResolutionCommand
from app.application.services import AutoResolutionService
from app.domain.rules import is_resolution_confirmation


class FakeAutoResolutionPort:
    def __init__(self) -> None:
        self.executions = 0

    async def count_due(self, *, evaluated_at: datetime) -> int:
        del evaluated_at
        return 3

    async def resolve_due(self, *, evaluated_at: datetime, limit: int) -> int:
        del evaluated_at
        assert limit == 100
        self.executions += 1
        return 3


async def test_auto_resolution_is_preview_only_by_default() -> None:
    port = FakeAutoResolutionPort()
    result = await AutoResolutionService(port, execution_enabled=False).run(
        RunAutoResolutionCommand(datetime.now(UTC), limit=100)
    )

    assert result.due_count == 3
    assert result.resolved_count == 0
    assert not result.executed
    assert port.executions == 0


async def test_auto_resolution_requires_execution_guard() -> None:
    service = AutoResolutionService(FakeAutoResolutionPort(), execution_enabled=False)

    with pytest.raises(PermissionError, match="disabled"):
        await service.run(RunAutoResolutionCommand(datetime.now(UTC), execute=True, limit=100))


def test_customer_resolution_confirmation_is_conservative() -> None:
    assert is_resolution_confirmation("问题已解决，谢谢")
    assert is_resolution_confirmation("That solved it, thank you")
    assert not is_resolution_confirmation("Can you solve this problem?")
