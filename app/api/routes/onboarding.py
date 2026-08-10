from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request

from app.api.dependencies import authenticate_admin_request, get_container
from app.api.request_context import source_fingerprint
from app.api.schemas.onboarding import (
    CreateEnrollmentAuthorityRequest,
    CreateEnrollmentPolicyRequest,
    EnrollmentCodeListResponse,
    EnrollmentCodeResponse,
    IssueEnrollmentCodeRequest,
    IssueEnrollmentCodeResponse,
    IssueWorkspaceOnboardingCodeRequest,
    IssueWorkspaceOnboardingCodeResponse,
    ResendSelfServiceVerificationRequest,
    SelfServiceSignupRequest,
    SelfServiceSignupResponse,
    SelfServiceSignupStatusResponse,
    VerifySelfServiceEmailRequest,
    VerifySelfServiceEmailResponse,
)
from app.application.dto import (
    CreateEnrollmentAuthorityCommand,
    CreateEnrollmentPolicyCommand,
    GetSelfServiceSignupStatusQuery,
    IssueEnrollmentCodeCommand,
    IssueWorkspaceOnboardingCodeCommand,
    ListEnrollmentCodesQuery,
    ResendSelfServiceVerificationCommand,
    RevokeEnrollmentCodeCommand,
    StartSelfServiceSignupCommand,
    VerifySelfServiceEmailCommand,
)
from app.application.services import (
    EnrollmentAccessError,
    EnrollmentConflictError,
    EnrollmentRateLimitedError,
)
from app.bootstrap.container import Container

router = APIRouter(prefix="/v1/onboarding", tags=["onboarding"])
platform_router = APIRouter(prefix="/v1/platform", tags=["platform-enrollment"])


@router.post("/signup", response_model=SelfServiceSignupResponse)
async def signup(
    payload: SelfServiceSignupRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> SelfServiceSignupResponse:
    service = _employee_service_or_409(container)
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key is required")
    try:
        result = await service.start_signup(
            StartSelfServiceSignupCommand(
                email=payload.email,
                password=payload.password,
                display_name=payload.display_name,
                workspace_name=payload.workspace_name,
                enrollment_code=payload.enterprise_code,
                idempotency_key=idempotency_key,
                source_fingerprint=source_fingerprint(
                    request, trusted_proxy_cidrs=container.settings.trusted_proxy_cidrs
                ),
                correlation_id=request.state.correlation_id,
            )
        )
    except EnrollmentRateLimitedError as exc:
        raise HTTPException(
            status_code=429,
            detail="too many enrollment attempts; try again later",
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except EnrollmentConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except EnrollmentAccessError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SelfServiceSignupResponse(
        status=result.status,
        status_token=result.status_token,
        expires_at=result.expires_at.isoformat(),
    )


@router.post("/verify-email", response_model=VerifySelfServiceEmailResponse)
async def verify_email(
    payload: VerifySelfServiceEmailRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> VerifySelfServiceEmailResponse:
    service = _employee_service_or_409(container)
    try:
        result = await service.verify_email(
            VerifySelfServiceEmailCommand(
                verification_token=payload.verification_token,
                source_fingerprint=source_fingerprint(
                    request, trusted_proxy_cidrs=container.settings.trusted_proxy_cidrs
                ),
                correlation_id=request.state.correlation_id,
            )
        )
    except EnrollmentRateLimitedError as exc:
        raise HTTPException(
            status_code=429,
            detail="too many verification attempts; try again later",
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except EnrollmentAccessError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return VerifySelfServiceEmailResponse(
        status=result.status,
        tenant_id=result.tenant_id,
        workspace_name=result.workspace_name,
    )


@router.post("/resend-verification", response_model=SelfServiceSignupResponse)
async def resend_verification(
    payload: ResendSelfServiceVerificationRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> SelfServiceSignupResponse:
    service = _employee_service_or_409(container)
    try:
        result = await service.resend_verification(
            ResendSelfServiceVerificationCommand(
                status_token=payload.status_token,
                source_fingerprint=source_fingerprint(
                    request, trusted_proxy_cidrs=container.settings.trusted_proxy_cidrs
                ),
                correlation_id=request.state.correlation_id,
            )
        )
    except EnrollmentRateLimitedError as exc:
        raise HTTPException(
            status_code=429,
            detail="too many verification attempts; try again later",
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except EnrollmentAccessError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SelfServiceSignupResponse(
        status=result.status,
        status_token=result.status_token,
        expires_at=result.expires_at.isoformat(),
    )


@router.get("/status", response_model=SelfServiceSignupStatusResponse)
async def signup_status(
    status_token: Annotated[str, Query(min_length=20, max_length=300)],
    container: Annotated[Container, Depends(get_container)],
) -> SelfServiceSignupStatusResponse:
    service = _employee_service_or_409(container)
    try:
        result = await service.get_status(GetSelfServiceSignupStatusQuery(status_token))
    except EnrollmentAccessError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return SelfServiceSignupStatusResponse(
        status=result.status,
        expires_at=result.expires_at.isoformat(),
        tenant_id=result.tenant_id,
        workspace_name=result.workspace_name,
    )


@platform_router.post("/enrollment-authorities")
async def create_authority(
    payload: CreateEnrollmentAuthorityRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
):
    principal = await authenticate_admin_request(request, container)
    service = _service_or_409(container)
    try:
        authority = await service.create_authority(
            CreateEnrollmentAuthorityCommand(
                principal=principal,
                authority_id=payload.authority_id,
                name=payload.name,
                max_active_tenants=payload.max_active_tenants,
                max_total_tenants=payload.max_total_tenants,
                correlation_id=request.state.correlation_id,
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "authority_id": authority.authority_id,
        "name": authority.name,
        "status": authority.status,
        "max_active_tenants": authority.max_active_tenants,
        "max_total_tenants": authority.max_total_tenants,
    }


@platform_router.post("/enrollment-policies")
async def create_policy(
    payload: CreateEnrollmentPolicyRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
):
    principal = await authenticate_admin_request(request, container)
    service = _service_or_409(container)
    try:
        policy = await service.create_policy(
            CreateEnrollmentPolicyCommand(
                principal=principal,
                policy_id=payload.policy_id,
                authority_id=payload.authority_id,
                name=payload.name,
                allowed_email_domains=tuple(payload.allowed_email_domains),
                require_exact_email_binding=payload.require_exact_email_binding,
                default_plan_id=payload.default_plan_id,
                require_mfa=payload.require_mfa,
                site_limit=payload.site_limit,
                correlation_id=request.state.correlation_id,
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {
        "policy_id": policy.policy_id,
        "authority_id": policy.authority_id,
        "name": policy.name,
        "enabled": policy.enabled,
        "allowed_email_domains": list(policy.allowed_email_domains),
        "default_plan_id": policy.default_plan_id,
        "default_role": "tenant_owner",
    }


@platform_router.post("/enrollment-codes", response_model=IssueEnrollmentCodeResponse)
async def issue_code(
    payload: IssueEnrollmentCodeRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> IssueEnrollmentCodeResponse:
    principal = await authenticate_admin_request(request, container)
    service = _service_or_409(container)
    try:
        result = await service.issue_code(
            IssueEnrollmentCodeCommand(
                principal=principal,
                policy_id=payload.policy_id,
                target_email=payload.target_email,
                expires_in_hours=payload.expires_in_hours,
                correlation_id=request.state.correlation_id,
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return IssueEnrollmentCodeResponse(
        code=_code_response(result.code), enrollment_code=result.enrollment_code
    )


@platform_router.post(
    "/workspace-onboarding-codes",
    response_model=IssueWorkspaceOnboardingCodeResponse,
)
async def issue_workspace_onboarding_code(
    payload: IssueWorkspaceOnboardingCodeRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> IssueWorkspaceOnboardingCodeResponse:
    principal = await authenticate_admin_request(request, container)
    service = _service_or_409(container)
    try:
        result = await service.issue_workspace_onboarding_code(
            IssueWorkspaceOnboardingCodeCommand(
                principal=principal,
                target_email=payload.target_email,
                expires_in_hours=payload.expires_in_hours,
                site_limit=payload.site_limit,
                correlation_id=request.state.correlation_id,
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return IssueWorkspaceOnboardingCodeResponse(
        code=_code_response(result.code),
        enrollment_code=result.enrollment_code,
        signup_url=result.signup_url,
    )


@platform_router.get("/enrollment-codes", response_model=EnrollmentCodeListResponse)
async def list_codes(
    policy_id: str,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> EnrollmentCodeListResponse:
    principal = await authenticate_admin_request(request, container)
    service = _service_or_409(container)
    try:
        result = await service.list_codes(ListEnrollmentCodesQuery(principal, policy_id))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return EnrollmentCodeListResponse(items=[_code_response(item) for item in result.items])


@platform_router.post("/enrollment-codes/{code_id}/revoke")
@platform_router.post("/onboarding-records/{code_id}/revoke")
async def revoke_code(
    code_id: str,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
):
    principal = await authenticate_admin_request(request, container)
    service = _service_or_409(container)
    try:
        code = await service.revoke_code(
            RevokeEnrollmentCodeCommand(
                principal=principal,
                code_id=code_id,
                correlation_id=request.state.correlation_id,
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _code_response(code)


def _service_or_409(container: Container):
    if container.self_service_onboarding_service is None:
        raise HTTPException(status_code=409, detail="self-service signup is not enabled")
    return container.self_service_onboarding_service


def _employee_service_or_409(container: Container):
    if not (
        container.settings.self_service_signup_enabled
        and container.settings.employee_enrollment_enabled
    ):
        raise HTTPException(status_code=409, detail="self-service signup is not enabled")
    return _service_or_409(container)


def _code_response(code) -> EnrollmentCodeResponse:  # type: ignore[no-untyped-def]
    return EnrollmentCodeResponse(
        code_id=code.code_id,
        policy_id=code.policy_id,
        code_prefix=code.code_prefix,
        status=code.status,
        target_email=code.target_email,
        expires_at=code.expires_at.isoformat(),
        consumed_at=code.consumed_at.isoformat() if code.consumed_at else None,
    )


__all__ = ["platform_router", "router"]
