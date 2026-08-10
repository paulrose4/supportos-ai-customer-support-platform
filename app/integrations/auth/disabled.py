from app.domain.models import AuthenticatedPrincipal


class DisabledAuthenticationAdapter:
    async def authenticate(self, correlation_id: str) -> AuthenticatedPrincipal:
        del correlation_id
        raise PermissionError("administrative authentication is disabled")
