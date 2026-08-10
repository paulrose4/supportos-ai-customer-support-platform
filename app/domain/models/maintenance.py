from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class BackupArtifactStatus:
    artifact_type: str
    completed_at: datetime
    file_name: str
    size_bytes: int
    sha256: str
    restore_verified_at: datetime | None = None
