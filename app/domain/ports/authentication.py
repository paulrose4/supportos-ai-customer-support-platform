from typing import Protocol

from app.domain.models import AuthenticatedPrincipal


class AuthenticationPort(Protocol):
    async def authenticate(self, correlation_id: str) -> AuthenticatedPrincipal: ...


class WidgetSiteAuthenticationPort(Protocol):
    async def authenticate_site(
        self,
        *,
        site_key: str,
        correlation_id: str,
    ) -> AuthenticatedPrincipal: ...
