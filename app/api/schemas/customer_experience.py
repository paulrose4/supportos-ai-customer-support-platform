from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class WidgetConfigPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    welcome_message: str = Field(min_length=1, max_length=500)
    online_message: str = Field(min_length=1, max_length=500)
    offline_message: str = Field(min_length=1, max_length=500)
    business_timezone: str = Field(min_length=1, max_length=100)
    business_hours: dict[str, str] = Field(default_factory=dict)
    holidays: list[str] = Field(default_factory=list, max_length=100)
    offline_form_enabled: bool = True
    primary_color: str = "#2563eb"
    position: Literal["left", "right"] = "right"
    agent_name: str = Field(min_length=1, max_length=100)
    agent_avatar_url: str | None = Field(default=None, max_length=500)
    mobile_enabled: bool = True
    default_language: str = Field(min_length=2, max_length=20)
    handoff_timeout_seconds: int = Field(default=120, ge=30, le=3600)
    csat_enabled: bool = True
    customer_address_mode: Literal["formal", "neutral", "friendly"] = "neutral"
    introduce_on_first_turn: bool = True
    launcher_asset_id: str | None = Field(default=None, max_length=100)
    launcher_image_fit: Literal["contain", "cover"] = "contain"
    agent_avatar_asset_id: str | None = Field(default=None, max_length=100)


class SaveWidgetDraftRequest(WidgetConfigPayload):
    idempotency_key: str = Field(min_length=1, max_length=200)


class WidgetVersionActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: str = Field(min_length=1, max_length=100)
    idempotency_key: str = Field(min_length=1, max_length=200)


class AutomationConditionsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site_id: str | None = None
    page_path_prefix: str | None = None
    business_hours: bool | None = None
    user_intent: str | None = None
    minimum_risk_level: int | None = Field(default=None, ge=0, le=3)
    authenticated: bool | None = None
    minimum_dwell_seconds: int | None = Field(default=None, ge=0, le=86400)
    has_assignee: bool | None = None
    has_ticket: bool | None = None


class AutomationActionsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queue_id: str | None = None
    priority: Literal["low", "normal", "high", "urgent"] | None = None
    tags: list[str] = Field(default_factory=list, max_length=20)
    create_ticket: bool = False
    direct_handoff: bool = False


class SaveAutomationRuleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str | None = Field(default=None, max_length=100)
    name: str = Field(min_length=1, max_length=120)
    enabled: bool = True
    sort_order: int = Field(default=100, ge=0, le=10000)
    conditions: AutomationConditionsPayload = Field(default_factory=AutomationConditionsPayload)
    actions: AutomationActionsPayload
    idempotency_key: str = Field(min_length=1, max_length=200)


class AutomationFactsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site_id: str
    page_path: str = "/"
    within_business_hours: bool = True
    user_intent: str | None = None
    risk_level: int = Field(default=0, ge=0, le=3)
    authenticated: bool = False
    dwell_seconds: int = Field(default=0, ge=0, le=86400)
    has_assignee: bool = False
    has_ticket: bool = False


class TestAutomationRuleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conditions: AutomationConditionsPayload
    facts: AutomationFactsPayload


class DeleteAutomationRuleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=200)


class CreateKnowledgeGapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Literal["missing_knowledge", "incorrect_answer"]
    summary: str = Field(min_length=1, max_length=2000)
    idempotency_key: str = Field(min_length=1, max_length=200)


class ResolveKnowledgeGapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolution_note: str = Field(min_length=1, max_length=2000)
    idempotency_key: str = Field(min_length=1, max_length=200)


class PublicSatisfactionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_token: str = Field(min_length=32, max_length=2048)
    conversation_id: str = Field(min_length=1, max_length=100)
    score: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=1000)
    request_id: str = Field(min_length=1, max_length=100)


class PublicOfflineMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_token: str = Field(min_length=32, max_length=2048)
    conversation_id: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=254)
    message: str = Field(min_length=1, max_length=5000)
    page_path: str = Field(default="/", min_length=1, max_length=500)
    request_id: str = Field(min_length=1, max_length=100)
