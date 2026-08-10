from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import get_container
from app.api.schemas import HealthResponse
from app.bootstrap.container import Container

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live", response_model=HealthResponse)
async def liveness(container: Annotated[Container, Depends(get_container)]) -> HealthResponse:
    return HealthResponse(status="ok", service=container.settings.app_name)


@router.get("/ready", response_model=HealthResponse)
async def readiness(container: Annotated[Container, Depends(get_container)]) -> HealthResponse:
    result = await container.readiness_service.execute()
    if not result.is_ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="required dependencies are unavailable",
        )
    return HealthResponse(status="ready", service=container.settings.app_name)
