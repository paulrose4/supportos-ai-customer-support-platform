from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.dependencies import authenticate_admin_request, get_container
from app.api.schemas.system_status import (
    BackupStatusResponse,
    SystemStatusConfigurationResponse,
    SystemStatusResponse,
)
from app.application.dto import GetSystemStatusQuery
from app.bootstrap.container import Container

router = APIRouter(prefix="/v1/admin/system", tags=["system-status"])


@router.get("/status", response_model=SystemStatusResponse)
async def get_system_status(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> SystemStatusResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await container.system_status_service.get_status(GetSystemStatusQuery(principal))
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    configuration = result.configuration
    return SystemStatusResponse(
        is_ready=result.is_ready,
        failed_dependencies=list(result.failed_dependencies),
        configuration=SystemStatusConfigurationResponse(
            app_env=configuration.app_env,
            auth_mode=configuration.auth_mode,
            llm_provider=configuration.llm_provider,
            embedding_provider=configuration.embedding_provider,
            realtime_backend=configuration.realtime_backend,
            presence_backend=configuration.presence_backend,
            horizontal_scaling_ready=configuration.horizontal_scaling_ready,
            build_sha=configuration.build_sha,
            state_schema_version=configuration.state_schema_version,
            chat_model=configuration.chat_model,
            turn_planner_prompt_version=configuration.turn_planner_prompt_version,
            response_renderer_prompt_version=configuration.response_renderer_prompt_version,
        ),
        metrics=result.metrics,
        backups=[
            BackupStatusResponse(
                artifact_type=item.artifact_type,
                state=item.state,
                completed_at=item.completed_at.isoformat() if item.completed_at else None,
                age_hours=item.age_hours,
                file_name=item.file_name,
                size_bytes=item.size_bytes,
                sha256=item.sha256,
                restore_verified_at=(
                    item.restore_verified_at.isoformat() if item.restore_verified_at else None
                ),
            )
            for item in result.backups
        ],
    )
