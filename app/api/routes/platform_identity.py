from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.dependencies import authenticate_admin_request, get_container
from app.api.schemas.platform_identity import (
    AssignPlatformRoleRequest,
    CreatedTenantInvitationResponse,
    CreatePlatformTenantRequest,
    CreateTenantInvitationRequest,
    PlatformActivityResponse,
    PlatformMembershipListResponse,
    PlatformOnboardingRecordListResponse,
    PlatformOnboardingRecordResponse,
    PlatformSiteListResponse,
    PlatformSiteResponse,
    PlatformSummaryResponse,
    PlatformTenantListResponse,
    PlatformTenantResponse,
    PlatformUserListResponse,
    PlatformUserResponse,
    TenantInvitationListResponse,
    TenantInvitationResponse,
    TenantMembershipResponse,
    UpsertTenantMembershipRequest,
)
from app.application.dto import (
    AssignPlatformRoleCommand,
    CreateTenantCommand,
    CreateTenantInvitationCommand,
    GetPlatformSummaryQuery,
    GetPlatformTenantQuery,
    ListPlatformMembershipsQuery,
    ListPlatformOnboardingRecordsQuery,
    ListPlatformSiteRecordsQuery,
    ListPlatformTenantRecordsQuery,
    ListPlatformUserRecordsQuery,
    ListTenantInvitationsQuery,
    RevokePlatformRoleCommand,
    RevokeTenantInvitationCommand,
    UpsertTenantMembershipCommand,
)
from app.bootstrap.container import Container

router = APIRouter(prefix="/v1/platform", tags=["platform administration"])


@router.get("/summary", response_model=PlatformSummaryResponse)
async def get_platform_summary(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> PlatformSummaryResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await _service(container).get_summary(GetPlatformSummaryQuery(principal))
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    summary = result.summary
    return PlatformSummaryResponse(
        active_workspace_count=summary.active_workspace_count,
        user_count=summary.user_count,
        pending_onboarding_count=summary.pending_onboarding_count,
        attention_count=summary.attention_count,
        expiring_code_count=summary.expiring_code_count,
        failed_email_count=summary.failed_email_count,
        orphan_workspace_count=summary.orphan_workspace_count,
        recent_activity=[
            PlatformActivityResponse(
                event_type=item.event_type,
                actor_subject_id=item.actor_subject_id,
                resource_type=item.resource_type,
                resource_id=item.resource_id,
                details=item.details,
                created_at=item.created_at.isoformat(),
            )
            for item in summary.recent_activity
        ],
    )


@router.post(
    "/tenants/{tenant_id}/invitations",
    response_model=CreatedTenantInvitationResponse,
)
async def create_invitation(
    tenant_id: str,
    payload: CreateTenantInvitationRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> CreatedTenantInvitationResponse:
    principal = await authenticate_admin_request(request, container)
    service = _invitation_service(container)
    try:
        result = await service.create_invitation(
            CreateTenantInvitationCommand(
                principal=principal,
                tenant_id=tenant_id,
                email=payload.email,
                roles=frozenset(payload.roles),
                expires_in_hours=payload.expires_in_hours,
                correlation_id=request.state.correlation_id,
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    invitation = result.invitation
    return CreatedTenantInvitationResponse(
        **_invitation_response(invitation).model_dump(),
        invitation_url=result.invitation_url,
        email_sent=result.email_sent,
    )


@router.get(
    "/tenants/{tenant_id}/invitations",
    response_model=TenantInvitationListResponse,
)
async def list_invitations(
    tenant_id: str,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> TenantInvitationListResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await _invitation_service(container).list_invitations(
            ListTenantInvitationsQuery(principal=principal, tenant_id=tenant_id)
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return TenantInvitationListResponse(items=[_invitation_response(item) for item in result.items])


@router.delete(
    "/tenants/{tenant_id}/invitations/{invitation_id}",
    response_model=TenantInvitationResponse,
)
async def revoke_invitation(
    tenant_id: str,
    invitation_id: str,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> TenantInvitationResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        invitation = await _invitation_service(container).revoke_invitation(
            RevokeTenantInvitationCommand(
                principal=principal,
                tenant_id=tenant_id,
                invitation_id=invitation_id,
                correlation_id=request.state.correlation_id,
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _invitation_response(invitation)


@router.get("/tenants", response_model=PlatformTenantListResponse)
async def list_tenants(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
    q: Annotated[str, Query(max_length=200)] = "",
    workspace_status: Annotated[str, Query(alias="status")] = "",
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> PlatformTenantListResponse:
    principal = await authenticate_admin_request(request, container)
    service = _service(container)
    try:
        result = await service.list_tenant_records(
            ListPlatformTenantRecordsQuery(
                principal=principal,
                search=q,
                status=workspace_status,
                cursor=cursor,
                limit=limit,
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return PlatformTenantListResponse(
        items=[_tenant_record_response(item) for item in result.items],
        total=result.total,
        next_cursor=result.next_cursor,
    )


@router.get("/tenants/{tenant_id}", response_model=PlatformTenantResponse)
async def get_tenant(
    tenant_id: str,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> PlatformTenantResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        item = await _service(container).get_tenant_record(
            GetPlatformTenantQuery(principal=principal, tenant_id=tenant_id)
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _tenant_record_response(item)


@router.get("/sites", response_model=PlatformSiteListResponse)
async def list_platform_sites(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
    q: Annotated[str, Query(max_length=200)] = "",
    tenant_id: Annotated[str | None, Query(max_length=100)] = None,
    site_status: Annotated[str, Query(alias="status")] = "",
    verification_status: Annotated[str, Query(max_length=30)] = "",
    include_disabled: bool = True,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> PlatformSiteListResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await _service(container).list_site_records(
            ListPlatformSiteRecordsQuery(
                principal=principal,
                search=q,
                tenant_id=tenant_id,
                status=site_status,
                verification_status=verification_status,
                include_disabled=include_disabled,
                cursor=cursor,
                limit=limit,
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return PlatformSiteListResponse(
        items=[_platform_site_response(item) for item in result.items],
        total=result.total,
        next_cursor=result.next_cursor,
    )


@router.get("/tenants/{tenant_id}/sites", response_model=PlatformSiteListResponse)
async def list_platform_tenant_sites(
    tenant_id: str,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
    q: Annotated[str, Query(max_length=200)] = "",
    site_status: Annotated[str, Query(alias="status")] = "",
    verification_status: Annotated[str, Query(max_length=30)] = "",
    include_disabled: bool = True,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> PlatformSiteListResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await _service(container).list_site_records(
            ListPlatformSiteRecordsQuery(
                principal=principal,
                search=q,
                tenant_id=tenant_id,
                status=site_status,
                verification_status=verification_status,
                include_disabled=include_disabled,
                cursor=cursor,
                limit=limit,
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return PlatformSiteListResponse(
        items=[_platform_site_response(item) for item in result.items],
        total=result.total,
        next_cursor=result.next_cursor,
    )


@router.get(
    "/tenants/{tenant_id}/members",
    response_model=PlatformMembershipListResponse,
)
async def list_tenant_members(
    tenant_id: str,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> PlatformMembershipListResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await _service(container).list_memberships(
            ListPlatformMembershipsQuery(principal=principal, tenant_id=tenant_id)
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return PlatformMembershipListResponse(
        items=[
            TenantMembershipResponse(
                membership_id=item.membership_id,
                tenant_id=item.tenant_id,
                tenant_name=result.tenant_name,
                user_id=item.user_id,
                display_name=item.display_name,
                email=item.email,
                roles=list(item.roles),
                scopes=[],
                status=item.status,
            )
            for item in result.items
        ]
    )


@router.post("/tenants", response_model=PlatformTenantResponse)
async def create_tenant(
    payload: CreatePlatformTenantRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> PlatformTenantResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        item = await _service(container).create_tenant(
            CreateTenantCommand(principal, payload.tenant_id, payload.name)
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return PlatformTenantResponse(
        tenant_id=item.tenant_id,
        name=item.name,
        status=item.status,
        created_at=item.created_at.isoformat(),
        updated_at=item.updated_at.isoformat(),
    )


@router.get("/users", response_model=PlatformUserListResponse)
async def list_users(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
    q: Annotated[str, Query(max_length=200)] = "",
    user_status: Annotated[str, Query(alias="status")] = "",
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> PlatformUserListResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await _service(container).list_user_records(
            ListPlatformUserRecordsQuery(
                principal=principal,
                search=q,
                status=user_status,
                cursor=cursor,
                limit=limit,
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return PlatformUserListResponse(
        items=[
            PlatformUserResponse(
                user_id=item.user_id,
                display_name=item.display_name,
                email=item.email,
                status=item.status,
                created_at=item.created_at.isoformat(),
                updated_at=item.updated_at.isoformat(),
                workspace_count=item.workspace_count,
                workspace_names=list(item.workspace_names),
                disabled_workspace_count=item.disabled_workspace_count,
                disabled_workspace_names=list(item.disabled_workspace_names),
                platform_roles=list(item.platform_roles),
                last_login_at=(item.last_login_at.isoformat() if item.last_login_at else None),
            )
            for item in result.items
        ],
        total=result.total,
        next_cursor=result.next_cursor,
    )


@router.get(
    "/onboarding-records",
    response_model=PlatformOnboardingRecordListResponse,
)
async def list_onboarding_records(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
    q: Annotated[str, Query(max_length=200)] = "",
    record_status: Annotated[str, Query(alias="status")] = "",
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> PlatformOnboardingRecordListResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await _service(container).list_onboarding_records(
            ListPlatformOnboardingRecordsQuery(
                principal=principal,
                search=q,
                status=record_status,
                cursor=cursor,
                limit=limit,
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return PlatformOnboardingRecordListResponse(
        items=[_onboarding_record_response(item) for item in result.items],
        total=result.total,
        next_cursor=result.next_cursor,
    )


@router.put(
    "/tenants/{tenant_id}/members/{user_id}",
    response_model=TenantMembershipResponse,
)
async def upsert_membership(
    tenant_id: str,
    user_id: str,
    payload: UpsertTenantMembershipRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> TenantMembershipResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        item = await _service(container).upsert_membership(
            UpsertTenantMembershipCommand(
                principal=principal,
                tenant_id=tenant_id,
                user_id=user_id,
                roles=frozenset(payload.roles),
                status=payload.status,
                correlation_id=request.state.correlation_id,
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return TenantMembershipResponse(
        membership_id=item.membership_id,
        tenant_id=item.tenant_id,
        tenant_name=item.tenant_name,
        user_id=item.user_id,
        roles=sorted(item.roles),
        scopes=sorted(item.scopes),
        status=item.status,
    )


@router.put("/users/{user_id}/platform-role", status_code=204)
async def assign_platform_role(
    user_id: str,
    payload: AssignPlatformRoleRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> None:
    principal = await authenticate_admin_request(request, container)
    try:
        await _service(container).assign_platform_role(
            AssignPlatformRoleCommand(
                principal=principal,
                user_id=user_id,
                role=payload.role,
                correlation_id=request.state.correlation_id,
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.delete("/users/{user_id}/platform-roles/{role}", status_code=204)
async def revoke_platform_role(
    user_id: str,
    role: str,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> None:
    principal = await authenticate_admin_request(request, container)
    try:
        await _service(container).revoke_platform_role(
            RevokePlatformRoleCommand(
                principal=principal,
                user_id=user_id,
                role=role,
                correlation_id=request.state.correlation_id,
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


def _tenant_record_response(item) -> PlatformTenantResponse:  # type: ignore[no-untyped-def]
    return PlatformTenantResponse(
        tenant_id=item.tenant_id,
        name=item.name,
        status=item.status,
        owner_email=item.owner_email,
        owner_names=list(item.owner_names),
        owner_emails=list(item.owner_emails),
        member_count=item.member_count,
        disabled_member_count=item.disabled_member_count,
        site_count=item.site_count,
        disabled_site_count=item.disabled_site_count,
        unverified_site_count=item.unverified_site_count,
        site_quota_used=item.site_quota_used,
        site_limit=item.site_limit,
        plan_id=item.plan_id,
        subscription_status=item.subscription_status,
        created_at=item.created_at.isoformat(),
        updated_at=item.updated_at.isoformat(),
        last_activity_at=(
            item.last_activity_at.isoformat() if item.last_activity_at is not None else None
        ),
    )


def _platform_site_response(item) -> PlatformSiteResponse:  # type: ignore[no-untyped-def]
    return PlatformSiteResponse(
        tenant_id=item.tenant_id,
        tenant_name=item.tenant_name,
        site_id=item.site_id,
        name=item.name,
        base_url=item.base_url,
        status=item.status,
        verification_status=item.verification_status,
        knowledge_publication_state=item.knowledge_publication_state,
        manager_names=list(item.manager_names),
        manager_emails=list(item.manager_emails),
        created_at=item.created_at.isoformat(),
        updated_at=item.updated_at.isoformat(),
    )


def _onboarding_record_response(item) -> PlatformOnboardingRecordResponse:  # type: ignore[no-untyped-def]
    return PlatformOnboardingRecordResponse(
        code_id=item.code_id,
        target_email=item.target_email,
        status=item.status,
        expires_at=item.expires_at.isoformat(),
        created_by=item.created_by,
        created_by_name=item.created_by_name,
        created_at=item.created_at.isoformat(),
        workspace_name=item.workspace_name,
        tenant_id=item.tenant_id,
        email_status=item.email_status,
        email_attempts=item.email_attempts,
        email_sent_at=(item.email_sent_at.isoformat() if item.email_sent_at else None),
        email_last_error=item.email_last_error,
    )


def _service(container: Container):  # type: ignore[no-untyped-def]
    if container.platform_identity_service is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="platform identity control plane is not enabled",
        )
    return container.platform_identity_service


def _invitation_service(container: Container):  # type: ignore[no-untyped-def]
    if container.invitation_management_service is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="invitation registration is not enabled",
        )
    return container.invitation_management_service


def _invitation_response(invitation) -> TenantInvitationResponse:  # type: ignore[no-untyped-def]
    return TenantInvitationResponse(
        invitation_id=invitation.invitation_id,
        tenant_id=invitation.tenant_id,
        tenant_name=invitation.tenant_name,
        email=invitation.display_email,
        roles=sorted(invitation.roles),
        status=invitation.status,
        expires_at=invitation.expires_at.isoformat(),
        created_at=invitation.created_at.isoformat(),
        redeemed_at=(
            invitation.redeemed_at.isoformat() if invitation.redeemed_at is not None else None
        ),
        revoked_at=(
            invitation.revoked_at.isoformat() if invitation.revoked_at is not None else None
        ),
    )
