from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    subject_id: str
    tenant_id: str
    roles: frozenset[str]
    scopes: frozenset[str]
    authentication_method: str
    authenticated_at: datetime
    correlation_id: str
    site_id: str | None = None
    preferred_language: str | None = None
    platform_roles: frozenset[str] = frozenset()
    site_domain: str | None = None
    agent_display_name: str | None = None
    agent_identity_type: str = "team"
    customer_address_mode: str = "neutral"
    introduce_on_first_turn: bool = True
    site_identity_version: str = "site-identity-v1"
    # Bound to a public-widget bearer token; absent for administrative principals.
    visitor_session_id: str | None = None

    @property
    def is_anonymous(self) -> bool:
        return self.authentication_method in {
            "anonymous",
            "public_widget_token",
            "public_presence_token",
            "widget_site_key",
            "wordpress_site_key",
        }
