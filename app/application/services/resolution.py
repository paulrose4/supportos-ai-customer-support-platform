from app.application.dto.resolution import AutoResolutionResult, RunAutoResolutionCommand
from app.domain.ports.resolution import AutoResolutionPort


class AutoResolutionService:
    def __init__(self, port: AutoResolutionPort, *, execution_enabled: bool) -> None:
        self._port = port
        self._execution_enabled = execution_enabled

    async def run(self, command: RunAutoResolutionCommand) -> AutoResolutionResult:
        if not 1 <= command.limit <= 10_000:
            raise ValueError("auto-resolution limit must be between 1 and 10000")
        due_count = await self._port.count_due(evaluated_at=command.evaluated_at)
        if not command.execute:
            return AutoResolutionResult(command.evaluated_at, due_count, 0, False)
        if not self._execution_enabled:
            raise PermissionError("auto-resolution execution is disabled")
        resolved_count = await self._port.resolve_due(
            evaluated_at=command.evaluated_at,
            limit=command.limit,
        )
        return AutoResolutionResult(command.evaluated_at, due_count, resolved_count, True)
