from app.application.services import CheckReadinessService


class HealthyDependency:
    async def check(self) -> None:
        return None


class FailedDependency:
    async def check(self) -> None:
        raise RuntimeError("unavailable")


async def test_readiness_succeeds_when_all_dependencies_are_healthy() -> None:
    service = CheckReadinessService(
        {"postgres": HealthyDependency(), "qdrant": HealthyDependency()}
    )

    result = await service.execute()

    assert result.is_ready
    assert result.failed_dependencies == ()


async def test_readiness_reports_failed_dependency_without_internal_error() -> None:
    service = CheckReadinessService({"postgres": HealthyDependency(), "qdrant": FailedDependency()})

    result = await service.execute()

    assert not result.is_ready
    assert result.failed_dependencies == ("qdrant",)
