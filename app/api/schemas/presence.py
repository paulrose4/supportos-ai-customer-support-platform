from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class WidgetPresenceRequest(BaseModel):
    visitor_id: str = Field(min_length=1, max_length=100)
    conversation_id: str | None = Field(default=None, max_length=100)
    page_path: str = Field(default="/", min_length=1, max_length=500)
    # Optional connector-provided taxonomy. URL parsing remains a server-side
    # fallback for older widgets, while trusted connectors can avoid guessing.
    page_kind: str | None = Field(
        default=None,
        pattern="^(home|category|product|comparison|shipping|payment|pricing|cart|checkout|order_confirmation|support|content|unknown)$",
    )
    page_title: str | None = Field(default=None, max_length=200)
    referrer: str | None = Field(default=None, max_length=1000)
    language: str | None = Field(default=None, max_length=35)
    timezone: str | None = Field(default=None, max_length=100)
    page_view_id: str | None = Field(default=None, min_length=1, max_length=100)
    widget_state: str | None = Field(default=None, pattern="^(closed|open)$")
    presence_source: str | None = Field(default=None, pattern="^(page_load|widget)$")
    runtime_version: str | None = Field(default=None, min_length=1, max_length=100)
    config_version: str | None = Field(default=None, min_length=1, max_length=100)
    connector_type: str | None = Field(
        default=None,
        pattern="^(public|wordpress|static_php|cloudflare_worker|legacy)$",
    )
    connector_version: str | None = Field(default=None, min_length=1, max_length=100)


class WidgetPresenceResponse(BaseModel):
    status: str = "ok"
    last_seen_at: datetime


class VisitorPresenceItem(BaseModel):
    site_id: str
    visitor_id: str
    conversation_id: str | None
    page_path: str
    page_kind: str | None = None
    last_seen_at: datetime
    first_seen_at: datetime
    page_title: str | None
    referrer: str | None
    ip_address: str | None
    country_code: str | None
    browser: str | None
    operating_system: str | None
    device_type: str | None
    language: str | None
    timezone: str | None
    page_view_count: int
    session_started_at: datetime
    current_page_entered_at: datetime
    last_page_view_id: str | None
    widget_state: str
    presence_source: str
    runtime_version: str | None = None
    config_version: str | None = None
    connector_type: str | None = None
    connector_version: str | None = None
    commercial_intent: int = Field(default=0, ge=0, le=100)
    intent_tier: Literal["hot", "warm", "nurture", "unknown"] = "unknown"
    operation_priority: Literal["P0", "P1", "P2"] = "P2"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence_grade: Literal["A", "B", "C"] = "C"
    queue_eligible: bool = False
    next_action: Literal[
        "monitor",
        "monitor_closely",
        "invite_chat",
        "continue_conversation",
        "answer_shipping",
        "answer_price",
        "answer_payment",
        "offer_assistance",
        "contact_now",
    ] = "monitor"
    signals: list[str] = Field(default_factory=list)
    freshness: Literal["current", "aging", "stale", "expired", "unknown"] = "unknown"
    rule_version: str = "lead-scoring.v1"
    scored_at: datetime | None = None
    current_page_dwell_seconds: int = Field(default=0, ge=0)
    session_active_dwell_seconds: int = Field(default=0, ge=0)
    session_age_seconds: int = Field(default=0, ge=0)
    data_coverage: list[str] = Field(default_factory=list)


class VisitorPresenceListResponse(BaseModel):
    items: list[VisitorPresenceItem] = Field(default_factory=list)
