from dataclasses import dataclass
from datetime import datetime

from app.domain.models import AuthenticatedPrincipal, WidgetConfig


@dataclass(frozen=True, slots=True)
class BootstrapPublicWidgetCommand:
    public_widget_id: str
    origin: str
    source_address: str
    correlation_id: str
    resume_token: str | None = None
    session_token: str | None = None


@dataclass(frozen=True, slots=True)
class BootstrapPublicWidgetResult:
    session_token: str
    expires_at: datetime
    primary_language: str
    widget_config: WidgetConfig
    is_online: bool
    resume_token: str | None = None
    resume_expires_at: datetime | None = None
    widget_config_version: str = "site-identity-v1"


@dataclass(frozen=True, slots=True)
class GetPublicWidgetAppearanceQuery:
    public_widget_id: str
    origin: str
    source_address: str
    correlation_id: str


@dataclass(frozen=True, slots=True)
class PublicWidgetAppearanceResult:
    widget_config: WidgetConfig
    widget_config_version: str
    is_online: bool


@dataclass(frozen=True, slots=True)
class AuthenticatePublicWidgetCommand:
    session_token: str
    origin: str
    source_address: str
    correlation_id: str
    operation: str
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class AuthenticatePublicWidgetResult:
    principal: AuthenticatedPrincipal


@dataclass(frozen=True, slots=True)
class AuthenticatePublicPresenceCommand:
    visitor_id: str
    origin: str
    source_address: str
    correlation_id: str
    public_widget_id: str | None = None
    presence_token: str | None = None


@dataclass(frozen=True, slots=True)
class AuthenticatePublicPresenceResult:
    principal: AuthenticatedPrincipal
    presence_token: str
    expires_at: datetime
