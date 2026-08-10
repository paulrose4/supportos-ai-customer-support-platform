from datetime import datetime
from typing import Protocol

from app.domain.models.customer_experience import (
    AutomationActions,
    AutomationExecution,
    AutomationFacts,
    AutomationRule,
    CustomerExperienceSummary,
    KnowledgeGap,
    WidgetConfig,
    WidgetConfigurationState,
)


class CustomerExperiencePort(Protocol):
    async def get_widget_configuration(
        self, *, tenant_id: str, site_id: str
    ) -> WidgetConfigurationState | None: ...

    async def save_widget_draft(
        self,
        *,
        tenant_id: str,
        site_id: str,
        version_id: str,
        config: WidgetConfig,
        actor_subject_id: str,
        correlation_id: str,
        idempotency_key: str,
        created_at: datetime,
    ) -> WidgetConfigurationState: ...

    async def publish_widget_version(
        self,
        *,
        tenant_id: str,
        site_id: str,
        version_id: str,
        actor_subject_id: str,
        correlation_id: str,
        idempotency_key: str,
        published_at: datetime,
    ) -> WidgetConfigurationState: ...

    async def rollback_widget_version(
        self,
        *,
        tenant_id: str,
        site_id: str,
        source_version_id: str,
        new_version_id: str,
        actor_subject_id: str,
        correlation_id: str,
        idempotency_key: str,
        published_at: datetime,
    ) -> WidgetConfigurationState: ...

    async def list_automation_rules(self, *, tenant_id: str) -> list[AutomationRule]: ...

    async def save_automation_rule(
        self,
        *,
        rule: AutomationRule,
        actor_subject_id: str,
        correlation_id: str,
        idempotency_key: str,
    ) -> AutomationRule: ...

    async def delete_automation_rule(
        self,
        *,
        tenant_id: str,
        rule_id: str,
        actor_subject_id: str,
        correlation_id: str,
        idempotency_key: str,
        deleted_at: datetime,
    ) -> bool: ...

    async def get_automation_facts(
        self,
        *,
        tenant_id: str,
        site_id: str,
        conversation_id: str,
        page_path: str,
        user_intent: str | None,
        risk_level: int,
        authenticated: bool,
        dwell_seconds: int,
        occurred_at: datetime,
    ) -> AutomationFacts: ...

    async def record_automation_execution(
        self,
        *,
        execution: AutomationExecution,
        actions: AutomationActions,
        actor_subject_id: str,
        correlation_id: str,
        idempotency_key: str,
    ) -> AutomationExecution: ...

    async def list_automation_executions(
        self, *, tenant_id: str, limit: int
    ) -> list[AutomationExecution]: ...

    async def submit_satisfaction(
        self,
        *,
        tenant_id: str,
        site_id: str,
        conversation_id: str,
        rating_id: str,
        score: int,
        comment: str | None,
        idempotency_key: str,
        created_at: datetime,
        visitor_session_id: str | None = None,
    ) -> None: ...

    async def create_offline_ticket(
        self,
        *,
        tenant_id: str,
        site_id: str,
        conversation_id: str,
        email: str,
        message: str,
        page_path: str,
        idempotency_key: str,
        created_at: datetime,
        visitor_session_id: str | None = None,
    ) -> str: ...

    async def list_knowledge_gaps(
        self, *, tenant_id: str, status: str | None, limit: int
    ) -> list[KnowledgeGap]: ...

    async def create_knowledge_gap(
        self,
        *,
        gap: KnowledgeGap,
        correlation_id: str,
        idempotency_key: str,
    ) -> KnowledgeGap: ...

    async def resolve_knowledge_gap(
        self,
        *,
        tenant_id: str,
        gap_id: str,
        actor_subject_id: str,
        correlation_id: str,
        resolution_note: str,
        idempotency_key: str,
        resolved_at: datetime,
    ) -> KnowledgeGap: ...

    async def experience_summary(
        self, *, tenant_id: str, days: int, site_id: str | None
    ) -> CustomerExperienceSummary: ...
