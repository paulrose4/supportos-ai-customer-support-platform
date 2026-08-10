from app.domain.models import AuthenticatedPrincipal


def require_tenant(principal: AuthenticatedPrincipal, tenant_id: str) -> None:
    if principal.tenant_id != tenant_id:
        raise PermissionError("tenant access denied")


def require_scope(principal: AuthenticatedPrincipal, scope: str) -> None:
    if scope not in principal.scopes:
        raise PermissionError("scope access denied")
