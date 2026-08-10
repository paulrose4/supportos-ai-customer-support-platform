from dataclasses import dataclass

from app.domain.models import AuthenticatedPrincipal
from app.domain.models.customer_experience import (
    AutomationActions,
    AutomationConditions,
    AutomationExecution,
    AutomationFacts,
    AutomationRule,
    CustomerExperienceSummary,
    KnowledgeGap,
    WidgetConfig,
    WidgetConfigurationState,
)


@dataclass(frozen=True, slots=True)
class GetWidgetConfigurationQuery:
    principal: AuthenticatedPrincipal
    site_id: str


@dataclass(frozen=True, slots=True)
class SaveWidgetDraftCommand:
    principal: AuthenticatedPrincipal
    site_id: str
    config: WidgetConfig
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class PublishWidgetVersionCommand:
    principal: AuthenticatedPrincipal
    site_id: str
    version_id: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class RollbackWidgetVersionCommand:
    principal: AuthenticatedPrincipal
    site_id: str
    version_id: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ListAutomationRulesQuery:
    principal: AuthenticatedPrincipal


@dataclass(frozen=True, slots=True)
class SaveAutomationRuleCommand:
    principal: AuthenticatedPrincipal
    rule_id: str | None
    name: str
    enabled: bool
    sort_order: int
    conditions: AutomationConditions
    actions: AutomationActions
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class DeleteAutomationRuleCommand:
    principal: AuthenticatedPrincipal
    rule_id: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class TestAutomationRuleCommand:
    principal: AuthenticatedPrincipal
    conditions: AutomationConditions
    facts: AutomationFacts


@dataclass(frozen=True, slots=True)
class AutomationTestResult:
    matched: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ApplyAutomationCommand:
    principal: AuthenticatedPrincipal
    conversation_id: str
    page_path: str
    user_intent: str | None
    risk_level: int
    dwell_seconds: int
    request_id: str


@dataclass(frozen=True, slots=True)
class ListAutomationExecutionsQuery:
    principal: AuthenticatedPrincipal
    limit: int = 50


@dataclass(frozen=True, slots=True)
class SubmitSatisfactionCommand:
    principal: AuthenticatedPrincipal
    conversation_id: str
    score: int
    comment: str | None
    request_id: str


@dataclass(frozen=True, slots=True)
class CreateOfflineTicketCommand:
    principal: AuthenticatedPrincipal
    conversation_id: str
    email: str
    message: str
    page_path: str
    request_id: str


@dataclass(frozen=True, slots=True)
class ListKnowledgeGapsQuery:
    principal: AuthenticatedPrincipal
    status: str | None = None
    limit: int = 100


@dataclass(frozen=True, slots=True)
class CreateKnowledgeGapCommand:
    principal: AuthenticatedPrincipal
    conversation_id: str
    category: str
    summary: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ResolveKnowledgeGapCommand:
    principal: AuthenticatedPrincipal
    gap_id: str
    resolution_note: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class GetExperienceSummaryQuery:
    principal: AuthenticatedPrincipal
    days: int = 30
    site_id: str | None = None


@dataclass(frozen=True, slots=True)
class CustomerExperienceResult:
    widget_configuration: WidgetConfigurationState | None = None
    automation_rules: tuple[AutomationRule, ...] = ()
    automation_executions: tuple[AutomationExecution, ...] = ()
    knowledge_gaps: tuple[KnowledgeGap, ...] = ()
    summary: CustomerExperienceSummary | None = None
