from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class WidgetConfigStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class KnowledgeGapStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"


@dataclass(frozen=True, slots=True)
class WidgetConfig:
    welcome_message: str
    online_message: str
    offline_message: str
    business_timezone: str
    business_hours: dict[str, str]
    holidays: tuple[str, ...]
    offline_form_enabled: bool
    primary_color: str
    position: str
    agent_name: str
    agent_avatar_url: str | None
    mobile_enabled: bool
    default_language: str
    handoff_timeout_seconds: int
    csat_enabled: bool
    customer_address_mode: str = "neutral"
    introduce_on_first_turn: bool = True
    launcher_asset_id: str | None = None
    launcher_image_fit: str = "contain"
    agent_avatar_asset_id: str | None = None


@dataclass(frozen=True, slots=True)
class WidgetConfigVersion:
    version_id: str
    tenant_id: str
    site_id: str
    version_number: int
    status: WidgetConfigStatus
    config: WidgetConfig
    created_by: str
    created_at: datetime
    published_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class WidgetConfigurationState:
    site_id: str
    published: WidgetConfigVersion | None
    draft: WidgetConfigVersion | None
    versions: tuple[WidgetConfigVersion, ...]


@dataclass(frozen=True, slots=True)
class AutomationConditions:
    site_id: str | None = None
    page_path_prefix: str | None = None
    business_hours: bool | None = None
    user_intent: str | None = None
    minimum_risk_level: int | None = None
    authenticated: bool | None = None
    minimum_dwell_seconds: int | None = None
    has_assignee: bool | None = None
    has_ticket: bool | None = None


@dataclass(frozen=True, slots=True)
class AutomationActions:
    queue_id: str | None = None
    priority: str | None = None
    tags: tuple[str, ...] = ()
    create_ticket: bool = False
    direct_handoff: bool = False


@dataclass(frozen=True, slots=True)
class AutomationRule:
    rule_id: str
    tenant_id: str
    name: str
    enabled: bool
    sort_order: int
    conditions: AutomationConditions
    actions: AutomationActions
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class AutomationFacts:
    site_id: str
    page_path: str
    within_business_hours: bool
    user_intent: str | None
    risk_level: int
    authenticated: bool
    dwell_seconds: int
    has_assignee: bool
    has_ticket: bool


@dataclass(frozen=True, slots=True)
class AutomationExecution:
    execution_id: str
    tenant_id: str
    rule_id: str
    conversation_id: str
    matched: bool
    reasons: tuple[str, ...]
    actions_applied: tuple[str, ...]
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class SatisfactionRating:
    rating_id: str
    tenant_id: str
    site_id: str
    conversation_id: str
    score: int
    comment: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class KnowledgeGap:
    gap_id: str
    tenant_id: str
    conversation_id: str
    source: str
    category: str
    summary: str
    status: KnowledgeGapStatus
    created_by: str
    created_at: datetime
    resolved_by: str | None = None
    resolution_note: str | None = None
    resolved_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class CustomerExperienceSummary:
    satisfaction_count: int
    average_satisfaction: float
    open_knowledge_gaps: int
    eligible_satisfaction_conversations: int = 0
    satisfaction_response_rate: float = 0.0
    satisfaction_sample_target: int = 100
    satisfaction_sample_target_met: bool = False
    csat_release_target_met: bool = False
