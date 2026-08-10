from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.dependencies import authenticate_admin_request, get_container
from app.api.schemas.identity import (
    AdminUserCreateRequest,
    AdminUserListResponse,
    AdminUserPasswordResetRequest,
    AdminUserResponse,
    AdminUserUpdateRequest,
)
from app.application.dto import (
    CreateAdminUserCommand,
    ListAdminUsersQuery,
    ResetAdminUserPasswordCommand,
    UpdateAdminUserCommand,
)
from app.application.services import AdminUserConflictError
from app.bootstrap.container import Container

router = APIRouter(prefix="/v1/admin/users", tags=["administrator-users"])


@router.get("", response_model=AdminUserListResponse)
async def list_admin_users(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> AdminUserListResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await container.admin_user_management_service.list_users(
            ListAdminUsersQuery(principal)
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return AdminUserListResponse(items=[_user_response(item) for item in result.items])


@router.post("", response_model=AdminUserResponse, status_code=status.HTTP_201_CREATED)
async def create_admin_user(
    payload: AdminUserCreateRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> AdminUserResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        user = await container.admin_user_management_service.create_user(
            CreateAdminUserCommand(
                principal=principal,
                username=payload.username,
                display_name=payload.display_name,
                password=payload.password,
                roles=frozenset(payload.roles),
                correlation_id=request.state.correlation_id,
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except AdminUserConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _user_response(user)


@router.put("/{user_id}", response_model=AdminUserResponse)
async def update_admin_user(
    user_id: str,
    payload: AdminUserUpdateRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> AdminUserResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        user = await container.admin_user_management_service.update_user(
            UpdateAdminUserCommand(
                principal=principal,
                user_id=user_id,
                display_name=payload.display_name,
                roles=frozenset(payload.roles),
                status=payload.status,
                correlation_id=request.state.correlation_id,
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except AdminUserConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _user_response(user)


@router.post("/{user_id}/reset-password", response_model=AdminUserResponse)
async def reset_admin_user_password(
    user_id: str,
    payload: AdminUserPasswordResetRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> AdminUserResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        user = await container.admin_user_management_service.reset_password(
            ResetAdminUserPasswordCommand(
                principal=principal,
                user_id=user_id,
                new_password=payload.new_password,
                correlation_id=request.state.correlation_id,
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except AdminUserConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _user_response(user)


def _user_response(user) -> AdminUserResponse:  # type: ignore[no-untyped-def]
    return AdminUserResponse(
        user_id=user.user_id,
        tenant_id=user.tenant_id,
        username=user.username,
        display_name=user.display_name,
        roles=sorted(user.roles),
        scopes=sorted(user.scopes),
        status=user.status,
        created_at=user.created_at.isoformat(),
        updated_at=user.updated_at.isoformat(),
    )
