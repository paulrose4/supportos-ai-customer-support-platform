from typing import Annotated
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse

from app.api.dependencies import authenticate_admin_request, get_container
from app.api.request_context import source_fingerprint
from app.api.schemas.identity import (
    AdminLoginRequest,
    AdminPasswordChangeRequest,
    AdminSessionItemResponse,
    AdminSessionListResponse,
    AdminSessionResponse,
    AdminSessionRevocationResponse,
    AdminUserResponse,
    EmailAuthenticationResponse,
    EmailLoginRequest,
    ExternalLoginProviderListResponse,
    ExternalLoginProviderResponse,
    InvitationPreviewRequest,
    InvitationPreviewResponse,
    InvitationRegistrationRequest,
    PasswordResetCompletionRequest,
    PasswordResetRequest,
    SelectWorkspaceRequest,
    WorkspaceListResponse,
    WorkspaceResponse,
)
from app.application.dto import (
    AuthenticateAdminCommand,
    ChangeAdminPasswordCommand,
    ChangeEmailPasswordCommand,
    CompleteExternalLoginCommand,
    EmailLoginCommand,
    ListAdminSessionsQuery,
    ListWorkspacesQuery,
    LoginAdminCommand,
    LogoutAdminCommand,
    RegisterWithInvitationCommand,
    RequestPasswordResetCommand,
    ResetEmailPasswordCommand,
    RevokeAdminSessionCommand,
    SelectWorkspaceCommand,
    StartExternalLoginCommand,
)
from app.application.services import (
    AdminLoginThrottledError,
    EmailLoginThrottledError,
    ExternalLoginAccessDeniedError,
    ExternalLoginConfigurationError,
    ExternalLoginStateError,
    InvitationAccessError,
)
from app.application.tenant_context import bind_tenant
from app.bootstrap.container import Container

router = APIRouter(prefix="/v1/auth", tags=["authentication"])


@router.post("/email/login", response_model=EmailAuthenticationResponse)
async def email_login(
    payload: EmailLoginRequest,
    request: Request,
    response: Response,
    container: Annotated[Container, Depends(get_container)],
) -> EmailAuthenticationResponse:
    service = container.email_identity_service
    if service is None or not container.settings.email_login_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="email login is not enabled",
        )
    try:
        result = await service.login(
            EmailLoginCommand(
                email=payload.email,
                password=payload.password,
                correlation_id=request.state.correlation_id,
                source_fingerprint=source_fingerprint(
                    request, trusted_proxy_cidrs=container.settings.trusted_proxy_cidrs
                ),
            )
        )
    except EmailLoginThrottledError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many login attempts; try again later",
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return _complete_email_authentication(response, container, result)


@router.post("/invitations/preview", response_model=InvitationPreviewResponse)
async def preview_invitation(
    payload: InvitationPreviewRequest,
    container: Annotated[Container, Depends(get_container)],
) -> InvitationPreviewResponse:
    service = container.email_identity_service
    if service is None or not container.settings.invite_registration_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="invitation registration is not enabled",
        )
    try:
        result = await service.preview_invitation(payload.invitation_token)
    except InvitationAccessError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return InvitationPreviewResponse(
        tenant_name=result.tenant_name,
        email=result.email,
        roles=list(result.roles),
        expires_at=result.expires_at.isoformat(),
    )


@router.post("/register", response_model=EmailAuthenticationResponse)
async def register_with_invitation(
    payload: InvitationRegistrationRequest,
    request: Request,
    response: Response,
    container: Annotated[Container, Depends(get_container)],
) -> EmailAuthenticationResponse:
    service = container.email_identity_service
    if (
        service is None
        or not container.settings.email_login_enabled
        or not container.settings.invite_registration_enabled
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="invitation registration is not enabled",
        )
    try:
        result = await service.register(
            RegisterWithInvitationCommand(
                invitation_token=payload.invitation_token,
                display_name=payload.display_name,
                password=payload.password,
                correlation_id=request.state.correlation_id,
                source_fingerprint=source_fingerprint(
                    request, trusted_proxy_cidrs=container.settings.trusted_proxy_cidrs
                ),
            )
        )
    except InvitationAccessError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return _complete_email_authentication(response, container, result)


@router.post("/password/forgot", status_code=204)
async def request_password_reset(
    payload: PasswordResetRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> None:
    service = container.email_identity_service
    if service is not None and container.settings.email_login_enabled:
        await service.request_password_reset(
            RequestPasswordResetCommand(
                email=payload.email,
                correlation_id=request.state.correlation_id,
            )
        )


@router.post("/password/reset", status_code=204)
async def reset_email_password(
    payload: PasswordResetCompletionRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> None:
    service = container.email_identity_service
    if service is None or not container.settings.email_login_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="email login is not enabled",
        )
    try:
        await service.reset_password(
            ResetEmailPasswordCommand(
                reset_token=payload.reset_token,
                new_password=payload.new_password,
                correlation_id=request.state.correlation_id,
            )
        )
    except InvitationAccessError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/login", response_model=AdminSessionResponse)
async def login(
    payload: AdminLoginRequest,
    request: Request,
    response: Response,
    container: Annotated[Container, Depends(get_container)],
) -> AdminSessionResponse:
    if container.admin_session_service is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="session authentication is not enabled",
        )
    if not container.settings.legacy_login_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="local password login is disabled",
        )
    bind_tenant(payload.tenant_id)
    try:
        result = await container.admin_session_service.login(
            LoginAdminCommand(
                tenant_id=payload.tenant_id,
                username=payload.username,
                password=payload.password,
                correlation_id=request.state.correlation_id,
                source_fingerprint=source_fingerprint(
                    request, trusted_proxy_cidrs=container.settings.trusted_proxy_cidrs
                ),
            )
        )
    except AdminLoginThrottledError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many login attempts; try again later",
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    response.set_cookie(
        key=container.settings.admin_session_cookie_name,
        value=result.session.token,
        max_age=container.settings.admin_session_ttl_seconds,
        expires=result.session.expires_at,
        httponly=True,
        secure=container.settings.admin_cookie_secure,
        samesite="strict",
        path="/",
    )
    return AdminSessionResponse(
        user=_user_response(result.session.user),
        expires_at=result.session.expires_at.isoformat(),
    )


@router.get("/providers", response_model=ExternalLoginProviderListResponse)
async def list_login_providers(
    container: Annotated[Container, Depends(get_container)],
) -> ExternalLoginProviderListResponse:
    providers = (
        container.external_identity_service.enabled_providers
        if container.external_identity_service is not None
        else ()
    )
    return ExternalLoginProviderListResponse(
        providers=[
            ExternalLoginProviderResponse(
                provider=provider,
                start_url=f"/v1/auth/providers/{provider}/start",
            )
            for provider in providers
        ],
        legacy_login_enabled=container.settings.legacy_login_enabled,
        email_login_enabled=container.settings.email_login_enabled,
        invite_registration_enabled=container.settings.invite_registration_enabled,
        self_service_signup_enabled=(
            container.settings.email_login_enabled
            and container.settings.employee_enrollment_enabled
            and container.settings.self_service_signup_enabled
        ),
        password_reset_enabled=(
            container.settings.email_login_enabled
            and container.settings.transactional_email_enabled
        ),
    )


@router.get("/providers/{provider}/start")
async def start_external_login(
    provider: str,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
    return_path: str = "/",
) -> RedirectResponse:
    service = container.external_identity_service
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="external login is not enabled",
        )
    try:
        result = await service.start_login(
            StartExternalLoginCommand(
                provider=provider,
                redirect_uri=_external_callback_uri(container, provider),
                return_path=return_path,
            )
        )
    except (ValueError, ExternalLoginConfigurationError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return RedirectResponse(result.authorization_url, status_code=status.HTTP_302_FOUND)


@router.get("/providers/{provider}/callback")
async def complete_external_login(
    provider: str,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
    code: str = "",
    state: str = "",
    error: str = "",
) -> RedirectResponse:
    service = container.external_identity_service
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="external login is not enabled",
        )
    if error or not code or not state:
        return RedirectResponse(
            _with_query("/", "auth_error", "authorization_denied"),
            status_code=status.HTTP_302_FOUND,
        )
    try:
        result = await service.complete_login(
            CompleteExternalLoginCommand(
                provider=provider,
                code=code,
                state=state,
                redirect_uri=_external_callback_uri(container, provider),
                source_fingerprint=source_fingerprint(
                    request, trusted_proxy_cidrs=container.settings.trusted_proxy_cidrs
                ),
                correlation_id=request.state.correlation_id,
            )
        )
    except ExternalLoginStateError:
        return RedirectResponse(
            _with_query("/", "auth_error", "invalid_login_state"),
            status_code=status.HTTP_302_FOUND,
        )
    except ExternalLoginAccessDeniedError:
        return RedirectResponse(
            _with_query("/", "auth_error", "workspace_access_denied"),
            status_code=status.HTTP_302_FOUND,
        )
    except (ConnectionError, PermissionError):
        return RedirectResponse(
            _with_query("/", "auth_error", "identity_provider_unavailable"),
            status_code=status.HTTP_302_FOUND,
        )

    redirect_path = result.return_path
    response = RedirectResponse(redirect_path, status_code=status.HTTP_302_FOUND)
    if result.session is not None:
        _set_admin_session_cookie(response, container, result.session)
    elif result.completion_token is not None:
        response.set_cookie(
            key=container.settings.login_completion_cookie_name,
            value=result.completion_token,
            max_age=container.settings.login_completion_grant_ttl_seconds,
            httponly=True,
            secure=container.settings.admin_cookie_secure,
            samesite="strict",
            path="/v1/auth",
        )
        response.headers["Location"] = _with_query(redirect_path, "workspace_selection", "required")
    return response


@router.get("/workspaces", response_model=WorkspaceListResponse)
async def list_workspaces(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> WorkspaceListResponse:
    service = container.external_identity_service
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="external login is not enabled",
        )
    principal = None
    session_token = request.cookies.get(container.settings.admin_session_cookie_name, "")
    if session_token and container.admin_session_service is not None:
        try:
            principal = (
                await container.admin_session_service.authenticate(
                    AuthenticateAdminCommand(session_token, request.state.correlation_id)
                )
            ).principal
        except PermissionError:
            principal = None
    completion_token = request.cookies.get(container.settings.login_completion_cookie_name)
    try:
        result = await service.list_workspaces(
            ListWorkspacesQuery(principal=principal, completion_token=completion_token)
        )
    except ExternalLoginAccessDeniedError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return WorkspaceListResponse(
        items=[
            WorkspaceResponse(
                tenant_id=item.tenant_id,
                name=item.tenant_name,
                roles=sorted(item.roles),
                active=item.tenant_id == result.active_tenant_id,
            )
            for item in result.items
        ]
    )


@router.post("/switch-tenant", response_model=AdminSessionResponse)
async def switch_tenant(
    payload: SelectWorkspaceRequest,
    request: Request,
    response: Response,
    container: Annotated[Container, Depends(get_container)],
) -> AdminSessionResponse:
    service = container.external_identity_service
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="external login is not enabled",
        )
    current_token = request.cookies.get(container.settings.admin_session_cookie_name, "")
    principal = None
    if current_token and container.admin_session_service is not None:
        try:
            principal = (
                await container.admin_session_service.authenticate(
                    AuthenticateAdminCommand(current_token, request.state.correlation_id)
                )
            ).principal
        except PermissionError:
            principal = None
    try:
        result = await service.select_workspace(
            SelectWorkspaceCommand(
                tenant_id=payload.tenant_id,
                source_fingerprint=source_fingerprint(
                    request, trusted_proxy_cidrs=container.settings.trusted_proxy_cidrs
                ),
                correlation_id=request.state.correlation_id,
                principal=principal,
                completion_token=request.cookies.get(
                    container.settings.login_completion_cookie_name
                ),
                current_session_token=current_token or None,
            )
        )
    except ExternalLoginAccessDeniedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    _set_admin_session_cookie(response, container, result.session)
    response.delete_cookie(
        container.settings.login_completion_cookie_name,
        path="/v1/auth",
        secure=container.settings.admin_cookie_secure,
        httponly=True,
        samesite="strict",
    )
    return AdminSessionResponse(
        user=_user_response(result.session.user),
        expires_at=result.session.expires_at.isoformat(),
    )


@router.get("/me", response_model=AdminSessionResponse)
async def me(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> AdminSessionResponse:
    if container.admin_session_service is not None:
        token = request.cookies.get(container.settings.admin_session_cookie_name, "")
        try:
            result = await container.admin_session_service.authenticate(
                AuthenticateAdminCommand(token, request.state.correlation_id)
            )
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
        return AdminSessionResponse(user=_user_response(result.user))
    principal = await authenticate_admin_request(request, container)
    return AdminSessionResponse(
        user=AdminUserResponse(
            user_id=principal.subject_id,
            tenant_id=principal.tenant_id,
            username=principal.subject_id,
            display_name=principal.subject_id,
            roles=sorted(principal.roles),
            scopes=sorted(principal.scopes),
        )
    )


@router.post("/change-password", status_code=204)
async def change_password(
    payload: AdminPasswordChangeRequest,
    request: Request,
    response: Response,
    container: Annotated[Container, Depends(get_container)],
) -> None:
    if container.admin_session_service is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="session authentication is not enabled",
        )
    token = request.cookies.get(container.settings.admin_session_cookie_name, "")
    try:
        authenticated = await container.admin_session_service.authenticate(
            AuthenticateAdminCommand(token, request.state.correlation_id)
        )
        if authenticated.principal.authentication_method == "email_password":
            if container.email_identity_service is None:
                raise PermissionError("email login is not enabled")
            await container.email_identity_service.change_password(
                ChangeEmailPasswordCommand(
                    session_token=token,
                    current_password=payload.current_password,
                    new_password=payload.new_password,
                    correlation_id=request.state.correlation_id,
                )
            )
        else:
            await container.admin_session_service.change_password(
                ChangeAdminPasswordCommand(
                    session_token=token,
                    current_password=payload.current_password,
                    new_password=payload.new_password,
                    correlation_id=request.state.correlation_id,
                )
            )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    response.delete_cookie(
        container.settings.admin_session_cookie_name,
        path="/",
        secure=container.settings.admin_cookie_secure,
        httponly=True,
        samesite="strict",
    )


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    container: Annotated[Container, Depends(get_container)],
) -> None:
    token = request.cookies.get(container.settings.admin_session_cookie_name, "")
    if container.admin_session_service is not None:
        await container.admin_session_service.logout(
            LogoutAdminCommand(token, request.state.correlation_id)
        )
    response.delete_cookie(
        container.settings.admin_session_cookie_name,
        path="/",
        secure=container.settings.admin_cookie_secure,
        httponly=True,
        samesite="strict",
    )


@router.get("/sessions", response_model=AdminSessionListResponse)
async def list_sessions(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> AdminSessionListResponse:
    if container.admin_session_service is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="session authentication is not enabled",
        )
    token = request.cookies.get(container.settings.admin_session_cookie_name, "")
    principal = await authenticate_admin_request(request, container)
    result = await container.admin_session_service.list_sessions(
        ListAdminSessionsQuery(
            principal=principal,
            current_session_token=token,
        )
    )
    return AdminSessionListResponse(
        items=[
            AdminSessionItemResponse(
                session_id=item.session_id,
                created_at=item.created_at.isoformat(),
                last_seen_at=item.last_seen_at.isoformat(),
                expires_at=item.expires_at.isoformat(),
                revoked_at=item.revoked_at.isoformat() if item.revoked_at else None,
                source_fingerprint_prefix=item.source_fingerprint_prefix,
                is_current=item.is_current,
            )
            for item in result.items
        ]
    )


@router.delete("/sessions/{session_id}", response_model=AdminSessionRevocationResponse)
async def revoke_session(
    session_id: str,
    request: Request,
    response: Response,
    container: Annotated[Container, Depends(get_container)],
) -> AdminSessionRevocationResponse:
    if container.admin_session_service is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="session authentication is not enabled",
        )
    token = request.cookies.get(container.settings.admin_session_cookie_name, "")
    principal = await authenticate_admin_request(request, container)
    try:
        result = await container.admin_session_service.revoke_managed_session(
            RevokeAdminSessionCommand(
                principal=principal,
                current_session_token=token,
                session_id=session_id,
                correlation_id=request.state.correlation_id,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if result.was_current:
        response.delete_cookie(
            container.settings.admin_session_cookie_name,
            path="/",
            secure=container.settings.admin_cookie_secure,
            httponly=True,
            samesite="strict",
        )
    return AdminSessionRevocationResponse(
        session_id=result.session_id,
        revoked=result.revoked,
        changed=result.changed,
        was_current=result.was_current,
    )


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
        authentication_method=user.authentication_method,
        platform_roles=sorted(user.platform_roles),
    )


def _external_callback_uri(container: Container, provider: str) -> str:
    return (
        f"{container.settings.admin_public_base_url.rstrip('/')}"
        f"/v1/auth/providers/{provider}/callback"
    )


def _set_admin_session_cookie(response: Response, container: Container, session) -> None:  # type: ignore[no-untyped-def]
    response.set_cookie(
        key=container.settings.admin_session_cookie_name,
        value=session.token,
        max_age=container.settings.admin_session_ttl_seconds,
        expires=session.expires_at,
        httponly=True,
        secure=container.settings.admin_cookie_secure,
        samesite="strict",
        path="/",
    )


def _complete_email_authentication(
    response: Response,
    container: Container,
    result,
) -> EmailAuthenticationResponse:  # type: ignore[no-untyped-def]
    if result.session is not None:
        _set_admin_session_cookie(response, container, result.session)
        return EmailAuthenticationResponse(
            user=_user_response(result.session.user),
            expires_at=result.session.expires_at.isoformat(),
            workspace_selection_required=False,
        )
    if result.completion_token is None:
        raise RuntimeError("email authentication produced no session or completion token")
    response.set_cookie(
        key=container.settings.login_completion_cookie_name,
        value=result.completion_token,
        max_age=container.settings.login_completion_grant_ttl_seconds,
        httponly=True,
        secure=container.settings.admin_cookie_secure,
        samesite="strict",
        path="/v1/auth",
    )
    return EmailAuthenticationResponse(workspace_selection_required=True)


def _with_query(path: str, key: str, value: str) -> str:
    parts = urlsplit(path)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query[key] = value
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
