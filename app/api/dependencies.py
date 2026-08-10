from fastapi import HTTPException, Request, status

from app.application.dto import AuthenticateAdminCommand
from app.application.tenant_context import bind_tenant
from app.bootstrap.container import Container
from app.domain.models import AuthenticatedPrincipal


def get_container(request: Request) -> Container:
    return request.app.state.container


async def authenticate_admin_request(
    request: Request, container: Container
) -> AuthenticatedPrincipal:
    if container.admin_session_service is None:
        try:
            principal = await container.authentication.authenticate(request.state.correlation_id)
            bind_tenant(principal.tenant_id)
            return principal
        except PermissionError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="administrative authentication is unavailable",
            ) from exc
    token = request.cookies.get(container.settings.admin_session_cookie_name, "")
    try:
        result = await container.admin_session_service.authenticate(
            AuthenticateAdminCommand(token, request.state.correlation_id)
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    bind_tenant(result.principal.tenant_id)
    return result.principal
