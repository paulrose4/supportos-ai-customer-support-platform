from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class VisitorPresence:
    tenant_id: str
    site_id: str
    visitor_id: str
    conversation_id: str | None
    page_path: str
    last_seen_at: datetime
    first_seen_at: datetime | None = None
    page_title: str | None = None
    referrer: str | None = None
    ip_address: str | None = None
    country_code: str | None = None
    user_agent: str | None = None
    browser: str | None = None
    operating_system: str | None = None
    device_type: str | None = None
    language: str | None = None
    timezone: str | None = None
    page_view_count: int = 1
    session_started_at: datetime | None = None
    current_page_entered_at: datetime | None = None
    last_page_view_id: str | None = None
    widget_state: str = "closed"
    presence_source: str = "widget"
    page_kind: str | None = None
    runtime_version: str | None = None
    config_version: str | None = None
    connector_type: str | None = None
    connector_version: str | None = None
    session_active_dwell_seconds: int = 0
