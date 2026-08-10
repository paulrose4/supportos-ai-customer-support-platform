from collections.abc import Mapping

from app.application.dto import ReadinessResult
from app.domain.ports import DependencyHealthPort


class CheckReadinessService:
    def __init__(self, dependencies: Mapping[str, DependencyHealthPort]) -> None:
        self._dependencies = dict(dependencies)

    async def execute(self) -> ReadinessResult:
        failures: list[str] = []
        for name, dependency in self._dependencies.items():
            try:
                await dependency.check()
            except Exception:
                failures.append(name)
        return ReadinessResult(not failures, tuple(failures))
