from typing import Protocol

from app.domain.models.maintenance import BackupArtifactStatus


class BackupStatusPort(Protocol):
    async def list_backup_statuses(self) -> list[BackupArtifactStatus]: ...
