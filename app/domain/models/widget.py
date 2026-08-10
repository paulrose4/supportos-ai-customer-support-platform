from dataclasses import dataclass
from datetime import datetime

from app.domain.models.customer_experience import WidgetConfig


@dataclass(frozen=True, slots=True)
class PublicWidgetSite:
    public_widget_id: str
    tenant_id: str
    site_id: str
    allowed_origins: tuple[str, ...]
    status: str
    daily_message_limit: int
    primary_language: str = "en"
    widget_config: WidgetConfig | None = None
    base_url: str = ""
    widget_config_version: str = "site-identity-v1"
    verification_status: str = "verified"
    auth_version: int = 1


@dataclass(frozen=True, slots=True)
class PublicWidgetTokenClaims:
    token_id: str
    public_widget_id: str
    origin: str
    scopes: frozenset[str]
    issued_at: datetime
    expires_at: datetime
    visitor_session_id: str
    auth_version: int = 1
    token_version: int = 2


@dataclass(frozen=True, slots=True)
class PublicPresenceTokenClaims:
    token_id: str
    public_widget_id: str
    origin: str
    visitor_id_hash: str
    issued_at: datetime
    expires_at: datetime
    auth_version: int = 1
    token_version: int = 3


@dataclass(frozen=True, slots=True)
class IssuedPublicWidgetToken:
    token: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class IssuedPublicWidgetSession:
    session_id: str
    resume_token: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class WidgetCapacityLease:
    member: str
    tenant_id: str
    site_id: str
