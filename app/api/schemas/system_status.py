from pydantic import BaseModel, Field


class SystemStatusConfigurationResponse(BaseModel):
    app_env: str
    auth_mode: str
    llm_provider: str
    embedding_provider: str
    realtime_backend: str
    presence_backend: str
    horizontal_scaling_ready: bool
    build_sha: str
    state_schema_version: int
    chat_model: str
    turn_planner_prompt_version: str
    response_renderer_prompt_version: str


class BackupStatusResponse(BaseModel):
    artifact_type: str
    state: str
    completed_at: str | None
    age_hours: float | None
    file_name: str | None
    size_bytes: int | None
    sha256: str | None
    restore_verified_at: str | None


class SystemStatusResponse(BaseModel):
    is_ready: bool
    failed_dependencies: list[str] = Field(default_factory=list)
    configuration: SystemStatusConfigurationResponse
    metrics: dict[str, float | int]
    backups: list[BackupStatusResponse] = Field(default_factory=list)
