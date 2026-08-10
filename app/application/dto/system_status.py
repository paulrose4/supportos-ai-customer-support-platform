from dataclasses import dataclass
from datetime import datetime

from app.domain.models import AuthenticatedPrincipal


@dataclass(frozen=True, slots=True)
class GetSystemStatusQuery:
    principal: AuthenticatedPrincipal


@dataclass(frozen=True, slots=True)
class SystemStatusConfiguration:
    app_env: str
    auth_mode: str
    llm_provider: str
    embedding_provider: str
    realtime_backend: str
    presence_backend: str
    horizontal_scaling_ready: bool
    build_sha: str = "development"
    state_schema_version: int = 10
    chat_model: str = "unknown"
    turn_planner_prompt_version: str = "turn-planner-v1"
    response_renderer_prompt_version: str = "response-brief-v1"


@dataclass(frozen=True, slots=True)
class BackupStatusResult:
    artifact_type: str
    state: str
    completed_at: datetime | None
    age_hours: float | None
    file_name: str | None
    size_bytes: int | None
    sha256: str | None
    restore_verified_at: datetime | None


@dataclass(frozen=True, slots=True)
class SystemStatusResult:
    is_ready: bool
    failed_dependencies: tuple[str, ...]
    configuration: SystemStatusConfiguration
    metrics: dict[str, float | int]
    backups: tuple[BackupStatusResult, ...]
