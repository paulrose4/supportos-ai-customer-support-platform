from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class ManagedSupportSite:
    site_id: str
    tenant_id: str
    public_widget_id: str
    name: str
    base_url: str
    allowed_origins: tuple[str, ...]
    widget_daily_message_limit: int
    status: str
    credential_key_prefix: str | None
    credential_status: str | None
    created_at: datetime
    updated_at: datetime
    primary_language: str = "en"
    verification_status: str = "verified"
    verification_method: str | None = None
    verification_token_prefix: str | None = None
    verification_expires_at: datetime | None = None
    verified_at: datetime | None = None
