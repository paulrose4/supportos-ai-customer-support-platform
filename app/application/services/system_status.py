from datetime import UTC, datetime

from app.application.dto.system_status import (
    BackupStatusResult,
    GetSystemStatusQuery,
    SystemStatusConfiguration,
    SystemStatusResult,
)
from app.application.services.health import CheckReadinessService
from app.domain.ports import BackupStatusPort, RequestMetricsPort
from app.domain.rules import require_scope


class SystemStatusService:
    def __init__(
        self,
        *,
        readiness: CheckReadinessService,
        metrics: RequestMetricsPort,
        configuration: SystemStatusConfiguration,
        backup_status: BackupStatusPort,
        backup_max_age_hours: int,
    ) -> None:
        self._readiness = readiness
        self._metrics = metrics
        self._configuration = configuration
        self._backup_status = backup_status
        self._backup_max_age_hours = backup_max_age_hours

    async def get_status(self, query: GetSystemStatusQuery) -> SystemStatusResult:
        require_scope(query.principal, "audit:read")
        readiness = await self._readiness.execute()
        backup_items = await self._backup_status.list_backup_statuses()
        by_type = {item.artifact_type: item for item in backup_items}
        now = datetime.now(UTC)
        backups: list[BackupStatusResult] = []
        for artifact_type in ("postgres", "qdrant"):
            item = by_type.get(artifact_type)
            if item is None:
                backups.append(
                    BackupStatusResult(
                        artifact_type=artifact_type,
                        state="missing",
                        completed_at=None,
                        age_hours=None,
                        file_name=None,
                        size_bytes=None,
                        sha256=None,
                        restore_verified_at=None,
                    )
                )
                continue
            completed_at = item.completed_at
            if completed_at.tzinfo is None:
                completed_at = completed_at.replace(tzinfo=UTC)
            age_hours = max(0.0, (now - completed_at).total_seconds() / 3600)
            backups.append(
                BackupStatusResult(
                    artifact_type=artifact_type,
                    state=("current" if age_hours <= self._backup_max_age_hours else "stale"),
                    completed_at=completed_at,
                    age_hours=age_hours,
                    file_name=item.file_name,
                    size_bytes=item.size_bytes,
                    sha256=item.sha256,
                    restore_verified_at=item.restore_verified_at,
                )
            )
        return SystemStatusResult(
            is_ready=readiness.is_ready,
            failed_dependencies=readiness.failed_dependencies,
            configuration=self._configuration,
            metrics=self._metrics.snapshot(),
            backups=tuple(backups),
        )
