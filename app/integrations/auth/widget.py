from dataclasses import dataclass
from datetime import UTC, datetime
from secrets import compare_digest

from app.domain.models import AuthenticatedPrincipal


@dataclass(frozen=True, slots=True)
class WidgetSiteCredential:
    site_key: str
    tenant_id: str
    site_id: str
    primary_language: str = "en"
    site_domain: str | None = None
    agent_display_name: str | None = None
    customer_address_mode: str = "neutral"
    introduce_on_first_turn: bool = True
    site_identity_version: str = "site-identity-v1"


class StaticWidgetSiteAuthenticationAdapter:
    def __init__(self, site_keys: dict[str, str | dict[str, str]]) -> None:
        credentials: list[WidgetSiteCredential] = []
        for site_key, value in site_keys.items():
            if isinstance(value, str):
                tenant_id = value
                site_id = "default-site"
                primary_language = "en"
                site_domain = None
                agent_display_name = None
                customer_address_mode = "neutral"
                introduce_on_first_turn = True
                site_identity_version = "site-identity-v1"
            else:
                tenant_id = str(value.get("tenant_id") or "")
                site_id = str(value.get("site_id") or "")
                primary_language = str(value.get("primary_language") or "en")
                site_domain = str(value.get("site_domain") or "") or None
                agent_display_name = str(value.get("agent_display_name") or "") or None
                customer_address_mode = str(value.get("customer_address_mode") or "neutral")
                introduce_on_first_turn = bool(value.get("introduce_on_first_turn", True))
                site_identity_version = str(
                    value.get("site_identity_version") or "site-identity-v1"
                )
            if site_key and tenant_id and site_id:
                credentials.append(
                    WidgetSiteCredential(
                        site_key,
                        tenant_id,
                        site_id,
                        primary_language,
                        site_domain,
                        agent_display_name,
                        customer_address_mode,
                        introduce_on_first_turn,
                        site_identity_version,
                    )
                )
        self._credentials = tuple(credentials)

    async def authenticate_site(
        self,
        *,
        site_key: str,
        correlation_id: str,
    ) -> AuthenticatedPrincipal:
        credential = self._credential_for_key(site_key)
        if credential is None:
            raise PermissionError("invalid widget site credential")
        return AuthenticatedPrincipal(
            subject_id="anonymous-widget-visitor",
            tenant_id=credential.tenant_id,
            roles=frozenset({"anonymous"}),
            scopes=frozenset({"knowledge:read"}),
            authentication_method="widget_site_key",
            authenticated_at=datetime.now(UTC),
            correlation_id=correlation_id,
            site_id=credential.site_id,
            preferred_language=credential.primary_language,
            site_domain=credential.site_domain,
            agent_display_name=credential.agent_display_name,
            customer_address_mode=credential.customer_address_mode,
            introduce_on_first_turn=credential.introduce_on_first_turn,
            site_identity_version=credential.site_identity_version,
        )

    def _credential_for_key(self, candidate: str) -> WidgetSiteCredential | None:
        matched: WidgetSiteCredential | None = None
        for credential in self._credentials:
            if compare_digest(candidate, credential.site_key):
                matched = credential
        return matched
