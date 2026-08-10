import asyncio
import json
from datetime import datetime
from pathlib import Path

from app.domain.models.maintenance import BackupArtifactStatus


class FileBackupStatusAdapter:
    def __init__(self, status_directory: Path) -> None:
        self._status_directory = status_directory

    async def list_backup_statuses(self) -> list[BackupArtifactStatus]:
        return await asyncio.to_thread(self._read_statuses)

    def _read_statuses(self) -> list[BackupArtifactStatus]:
        statuses: list[BackupArtifactStatus] = []
        for artifact_type in ("postgres", "qdrant"):
            path = self._status_directory / f"{artifact_type}.json"
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if payload.get("artifact_type") != artifact_type:
                    continue
                statuses.append(
                    BackupArtifactStatus(
                        artifact_type=artifact_type,
                        completed_at=datetime.fromisoformat(str(payload["completed_at"])),
                        file_name=Path(str(payload["file_name"])).name,
                        size_bytes=max(0, int(payload["size_bytes"])),
                        sha256=str(payload["sha256"]),
                        restore_verified_at=(
                            datetime.fromisoformat(str(payload["restore_verified_at"]))
                            if payload.get("restore_verified_at")
                            else None
                        ),
                    )
                )
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                continue
        return statuses
