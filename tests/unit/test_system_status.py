from datetime import UTC, datetime

import pytest

from app.application.dto import GetSystemStatusQuery, SystemStatusConfiguration
from app.application.services import CheckReadinessService, SystemStatusService
from app.domain.models import AuthenticatedPrincipal, BackupArtifactStatus
from app.observability import InMemoryRequestMetrics
from tests.fakes.adapters import AsyncCloser


class FakeBackupStatus:
    def __init__(self, items: list[BackupArtifactStatus] | None = None) -> None:
        self.items = items or []

    async def list_backup_statuses(self) -> list[BackupArtifactStatus]:
        return self.items


def principal(scopes: frozenset[str]) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        subject_id="admin-1",
        tenant_id="tenant-a",
        roles=frozenset({"auditor"}),
        scopes=scopes,
        authentication_method="admin_session",
        authenticated_at=datetime.now(UTC),
        correlation_id="correlation-1",
    )


async def test_system_status_combines_readiness_configuration_and_metrics() -> None:
    metrics = InMemoryRequestMetrics()
    metrics.record(method="GET", route="/health/live", status_code=200, duration_seconds=0.01)
    metrics.record(method="GET", route="/example", status_code=500, duration_seconds=0.03)
    service = SystemStatusService(
        readiness=CheckReadinessService({"postgres": AsyncCloser(), "qdrant": AsyncCloser()}),
        metrics=metrics,
        backup_status=FakeBackupStatus(
            [
                BackupArtifactStatus(
                    artifact_type="postgres",
                    completed_at=datetime.now(UTC),
                    file_name="postgres.dump",
                    size_bytes=1024,
                    sha256="a" * 64,
                )
            ]
        ),
        backup_max_age_hours=36,
        configuration=SystemStatusConfiguration(
            app_env="development",
            auth_mode="session",
            llm_provider="fake",
            embedding_provider="fake",
            realtime_backend="in_memory",
            presence_backend="in_memory",
            horizontal_scaling_ready=False,
        ),
    )

    result = await service.get_status(GetSystemStatusQuery(principal(frozenset({"audit:read"}))))

    assert result.is_ready
    assert result.failed_dependencies == ()
    assert result.metrics["request_count"] == 2
    assert result.metrics["server_error_count"] == 1
    assert result.metrics["average_latency_ms"] == pytest.approx(20.0)
    assert not result.configuration.horizontal_scaling_ready
    assert result.backups[0].state == "current"
    assert result.backups[1].state == "missing"


async def test_system_status_requires_audit_scope() -> None:
    service = SystemStatusService(
        readiness=CheckReadinessService({}),
        metrics=InMemoryRequestMetrics(),
        backup_status=FakeBackupStatus(),
        backup_max_age_hours=36,
        configuration=SystemStatusConfiguration(
            app_env="development",
            auth_mode="session",
            llm_provider="fake",
            embedding_provider="fake",
            realtime_backend="in_memory",
            presence_backend="in_memory",
            horizontal_scaling_ready=False,
        ),
    )

    with pytest.raises(PermissionError, match="scope access denied"):
        await service.get_status(GetSystemStatusQuery(principal(frozenset())))
