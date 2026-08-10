from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import NAMESPACE_URL, uuid4, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.models.customer_experience import (
    AutomationActions,
    AutomationConditions,
    AutomationExecution,
    AutomationFacts,
    AutomationRule,
    CustomerExperienceSummary,
    KnowledgeGap,
    KnowledgeGapStatus,
    WidgetConfig,
    WidgetConfigStatus,
    WidgetConfigurationState,
    WidgetConfigVersion,
)
from app.integrations.postgres.experience import record_satisfaction_experience
from app.integrations.postgres.models import (
    AuditEventModel,
    ConversationModel,
    HandoffRequestModel,
    MessageModel,
    SupportOperationRequestModel,
    SupportQueueModel,
    SupportSiteModel,
    SupportTicketModel,
)
from app.integrations.postgres.models.customer_experience import (
    AutomationExecutionModel,
    AutomationRuleModel,
    KnowledgeGapModel,
    SatisfactionRatingModel,
    WidgetConfigVersionModel,
)


class PostgreSQLCustomerExperienceAdapter:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_widget_configuration(
        self, *, tenant_id: str, site_id: str
    ) -> WidgetConfigurationState | None:
        async with self._session_factory() as session:
            return await _configuration_state(session, tenant_id, site_id)

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
    ) -> WidgetConfigurationState:
        operation = "widget_config.draft_saved"
        async with self._session_factory.begin() as session:
            replay = await _replayed_state(session, tenant_id, idempotency_key, operation, site_id)
            if replay is not None:
                return replay
            site = await _locked_site(session, tenant_id, site_id)
            if site is None:
                raise LookupError("site was not found")
            latest = await session.scalar(
                select(func.max(WidgetConfigVersionModel.version_number)).where(
                    WidgetConfigVersionModel.tenant_id == tenant_id,
                    WidgetConfigVersionModel.site_id == site_id,
                )
            )
            model = WidgetConfigVersionModel(
                tenant_id=tenant_id,
                site_id=site_id,
                version_id=version_id,
                version_number=int(latest or 0) + 1,
                status=WidgetConfigStatus.DRAFT.value,
                config=_config_payload(config),
                created_by=actor_subject_id,
                created_at=created_at,
                published_at=None,
            )
            session.add(model)
            _record_write(
                session,
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                operation=operation,
                resource_type="widget_config",
                resource_id=site_id,
                actor_subject_id=actor_subject_id,
                correlation_id=correlation_id,
                details={"version_id": version_id, "version_number": model.version_number},
                occurred_at=created_at,
            )
            await session.flush()
            state = await _configuration_state(session, tenant_id, site_id)
            if state is None:
                raise LookupError("site was not found")
            return state

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
    ) -> WidgetConfigurationState:
        operation = "widget_config.published"
        async with self._session_factory.begin() as session:
            replay = await _replayed_state(session, tenant_id, idempotency_key, operation, site_id)
            if replay is not None:
                return replay
            site = await _locked_site(session, tenant_id, site_id)
            if site is None:
                raise LookupError("site was not found")
            target = await session.scalar(
                select(WidgetConfigVersionModel)
                .where(
                    WidgetConfigVersionModel.tenant_id == tenant_id,
                    WidgetConfigVersionModel.site_id == site_id,
                    WidgetConfigVersionModel.version_id == version_id,
                )
                .with_for_update()
            )
            if target is None:
                raise LookupError("widget configuration version was not found")
            await _archive_published(session, tenant_id, site_id, except_version_id=version_id)
            target.status = WidgetConfigStatus.PUBLISHED.value
            target.published_at = published_at
            _record_write(
                session,
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                operation=operation,
                resource_type="widget_config",
                resource_id=site_id,
                actor_subject_id=actor_subject_id,
                correlation_id=correlation_id,
                details={
                    "version_id": version_id,
                    "version_number": target.version_number,
                    "public_widget_id": site.public_widget_id,
                },
                occurred_at=published_at,
            )
            await session.flush()
            state = await _configuration_state(session, tenant_id, site_id)
            if state is None:
                raise LookupError("site was not found")
            return state

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
    ) -> WidgetConfigurationState:
        operation = "widget_config.rolled_back"
        async with self._session_factory.begin() as session:
            replay = await _replayed_state(session, tenant_id, idempotency_key, operation, site_id)
            if replay is not None:
                return replay
            site = await _locked_site(session, tenant_id, site_id)
            if site is None:
                raise LookupError("site was not found")
            source = await session.scalar(
                select(WidgetConfigVersionModel).where(
                    WidgetConfigVersionModel.tenant_id == tenant_id,
                    WidgetConfigVersionModel.site_id == site_id,
                    WidgetConfigVersionModel.version_id == source_version_id,
                )
            )
            if source is None:
                raise LookupError("widget configuration version was not found")
            latest = await session.scalar(
                select(func.max(WidgetConfigVersionModel.version_number)).where(
                    WidgetConfigVersionModel.tenant_id == tenant_id,
                    WidgetConfigVersionModel.site_id == site_id,
                )
            )
            await _archive_published(session, tenant_id, site_id)
            restored = WidgetConfigVersionModel(
                tenant_id=tenant_id,
                site_id=site_id,
                version_id=new_version_id,
                version_number=int(latest or 0) + 1,
                status=WidgetConfigStatus.PUBLISHED.value,
                config=dict(source.config),
                created_by=actor_subject_id,
                created_at=published_at,
                published_at=published_at,
            )
            session.add(restored)
            _record_write(
                session,
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                operation=operation,
                resource_type="widget_config",
                resource_id=site_id,
                actor_subject_id=actor_subject_id,
                correlation_id=correlation_id,
                details={
                    "source_version_id": source_version_id,
                    "new_version_id": new_version_id,
                    "version_number": restored.version_number,
                    "public_widget_id": site.public_widget_id,
                },
                occurred_at=published_at,
            )
            await session.flush()
            state = await _configuration_state(session, tenant_id, site_id)
            if state is None:
                raise LookupError("site was not found")
            return state

    async def list_automation_rules(self, *, tenant_id: str) -> list[AutomationRule]:
        async with self._session_factory() as session:
            models = list(
                await session.scalars(
                    select(AutomationRuleModel)
                    .where(AutomationRuleModel.tenant_id == tenant_id)
                    .order_by(AutomationRuleModel.sort_order, AutomationRuleModel.created_at)
                )
            )
        return [_rule(model) for model in models]

    async def save_automation_rule(
        self,
        *,
        rule: AutomationRule,
        actor_subject_id: str,
        correlation_id: str,
        idempotency_key: str,
    ) -> AutomationRule:
        operation = "automation_rule.saved"
        async with self._session_factory.begin() as session:
            existing_write = await _operation(session, rule.tenant_id, idempotency_key)
            if existing_write is not None:
                _assert_operation(existing_write, operation, rule.rule_id)
                model = await _automation_rule(session, rule.tenant_id, rule.rule_id)
                if model is None:
                    raise LookupError("automation rule was not found")
                return _rule(model)
            if rule.actions.queue_id:
                queue = await session.scalar(
                    select(SupportQueueModel).where(
                        SupportQueueModel.tenant_id == rule.tenant_id,
                        SupportQueueModel.queue_id == rule.actions.queue_id,
                        SupportQueueModel.status == "active",
                    )
                )
                if queue is None:
                    raise LookupError("support queue was not found")
                if queue.site_id is not None and queue.site_id != rule.conditions.site_id:
                    raise ValueError(
                        "site-scoped support queues require a rule scoped to the same site"
                    )
            if rule.conditions.site_id is not None:
                site = await session.scalar(
                    select(SupportSiteModel).where(
                        SupportSiteModel.tenant_id == rule.tenant_id,
                        SupportSiteModel.site_id == rule.conditions.site_id,
                        SupportSiteModel.status == "active",
                    )
                )
                if site is None:
                    raise LookupError("support site was not found")
            model = await _automation_rule(session, rule.tenant_id, rule.rule_id, lock=True)
            if model is None:
                model = AutomationRuleModel(
                    tenant_id=rule.tenant_id,
                    rule_id=rule.rule_id,
                    name=rule.name,
                    enabled=rule.enabled,
                    sort_order=rule.sort_order,
                    conditions=asdict(rule.conditions),
                    actions=_actions_payload(rule.actions),
                    created_at=rule.created_at,
                    updated_at=rule.updated_at,
                )
                session.add(model)
            else:
                model.name = rule.name
                model.enabled = rule.enabled
                model.sort_order = rule.sort_order
                model.conditions = asdict(rule.conditions)
                model.actions = _actions_payload(rule.actions)
                model.updated_at = rule.updated_at
            _record_write(
                session,
                tenant_id=rule.tenant_id,
                idempotency_key=idempotency_key,
                operation=operation,
                resource_type="automation_rule",
                resource_id=rule.rule_id,
                actor_subject_id=actor_subject_id,
                correlation_id=correlation_id,
                details={"name": rule.name, "enabled": rule.enabled, "sort_order": rule.sort_order},
                occurred_at=rule.updated_at,
            )
            await session.flush()
            return _rule(model)

    async def delete_automation_rule(
        self,
        *,
        tenant_id: str,
        rule_id: str,
        actor_subject_id: str,
        correlation_id: str,
        idempotency_key: str,
        deleted_at: datetime,
    ) -> bool:
        operation = "automation_rule.deleted"
        async with self._session_factory.begin() as session:
            existing = await _operation(session, tenant_id, idempotency_key)
            if existing is not None:
                _assert_operation(existing, operation, rule_id)
                return True
            model = await _automation_rule(session, tenant_id, rule_id, lock=True)
            if model is not None:
                await session.delete(model)
            _record_write(
                session,
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                operation=operation,
                resource_type="automation_rule",
                resource_id=rule_id,
                actor_subject_id=actor_subject_id,
                correlation_id=correlation_id,
                details={"existed": model is not None},
                occurred_at=deleted_at,
            )
            return True

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
    ) -> AutomationFacts:
        async with self._session_factory() as session:
            conversation = await session.scalar(
                select(ConversationModel).where(
                    ConversationModel.tenant_id == tenant_id,
                    ConversationModel.conversation_id == conversation_id,
                    ConversationModel.site_id == site_id,
                )
            )
            if conversation is None:
                raise LookupError("conversation was not found")
            has_ticket = await session.scalar(
                select(SupportTicketModel.id).where(
                    SupportTicketModel.tenant_id == tenant_id,
                    SupportTicketModel.conversation_id == conversation_id,
                    SupportTicketModel.status.in_(("open", "pending", "assigned")),
                )
            )
            published = await session.scalar(
                select(WidgetConfigVersionModel)
                .where(
                    WidgetConfigVersionModel.tenant_id == tenant_id,
                    WidgetConfigVersionModel.site_id == site_id,
                    WidgetConfigVersionModel.status == WidgetConfigStatus.PUBLISHED.value,
                )
                .order_by(WidgetConfigVersionModel.version_number.desc())
            )
        return AutomationFacts(
            site_id=site_id,
            page_path=page_path,
            within_business_hours=_within_business_hours(
                widget_config_from_payload(published.config) if published is not None else None,
                occurred_at,
            ),
            user_intent=user_intent
            if user_intent in {"knowledge", "order", "ticket", "logistics", "refund"}
            else "other",
            risk_level=risk_level,
            authenticated=authenticated,
            dwell_seconds=dwell_seconds,
            has_assignee=conversation.assigned_agent_id is not None,
            has_ticket=has_ticket is not None,
        )

    async def record_automation_execution(
        self,
        *,
        execution: AutomationExecution,
        actions: AutomationActions,
        actor_subject_id: str,
        correlation_id: str,
        idempotency_key: str,
    ) -> AutomationExecution:
        async with self._session_factory.begin() as session:
            existing = await session.scalar(
                select(AutomationExecutionModel).where(
                    AutomationExecutionModel.tenant_id == execution.tenant_id,
                    AutomationExecutionModel.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                return _execution(existing)
            conversation = await session.scalar(
                select(ConversationModel)
                .where(
                    ConversationModel.tenant_id == execution.tenant_id,
                    ConversationModel.conversation_id == execution.conversation_id,
                )
                .with_for_update()
            )
            if conversation is None:
                raise LookupError("conversation was not found")
            if execution.matched:
                await _apply_actions(session, conversation, execution, actions)
            model = AutomationExecutionModel(
                tenant_id=execution.tenant_id,
                execution_id=execution.execution_id,
                rule_id=execution.rule_id,
                conversation_id=execution.conversation_id,
                matched=execution.matched,
                reasons=list(execution.reasons),
                actions_applied=list(execution.actions_applied),
                idempotency_key=idempotency_key,
                occurred_at=execution.occurred_at,
            )
            session.add(model)
            session.add(
                AuditEventModel(
                    tenant_id=execution.tenant_id,
                    event_id=str(uuid4()),
                    event_type="automation_rule.executed",
                    actor_subject_id=actor_subject_id,
                    correlation_id=correlation_id,
                    resource_type="conversation",
                    resource_id=execution.conversation_id,
                    details={
                        "rule_id": execution.rule_id,
                        "matched": execution.matched,
                        "reasons": list(execution.reasons),
                        "actions_applied": list(execution.actions_applied),
                    },
                    created_at=execution.occurred_at,
                )
            )
            await session.flush()
            return _execution(model)

    async def list_automation_executions(
        self, *, tenant_id: str, limit: int
    ) -> list[AutomationExecution]:
        async with self._session_factory() as session:
            models = list(
                await session.scalars(
                    select(AutomationExecutionModel)
                    .where(AutomationExecutionModel.tenant_id == tenant_id)
                    .order_by(AutomationExecutionModel.occurred_at.desc())
                    .limit(limit)
                )
            )
        return [_execution(model) for model in models]

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
    ) -> None:
        async with self._session_factory.begin() as session:
            conversation = await session.scalar(
                select(ConversationModel)
                .where(
                    ConversationModel.tenant_id == tenant_id,
                    ConversationModel.site_id == site_id,
                    ConversationModel.conversation_id == conversation_id,
                )
                .with_for_update()
            )
            if conversation is None:
                raise PermissionError("conversation is not available for this site")
            _require_visitor_session_access(conversation, visitor_session_id)
            existing = await session.scalar(
                select(SatisfactionRatingModel).where(
                    SatisfactionRatingModel.tenant_id == tenant_id,
                    SatisfactionRatingModel.conversation_id == conversation_id,
                )
            )
            if existing is not None:
                return
            if conversation.status != "resolved":
                raise PermissionError("only resolved site conversations can be rated")
            session.add_all(
                [
                    SatisfactionRatingModel(
                        tenant_id=tenant_id,
                        rating_id=rating_id,
                        site_id=site_id,
                        conversation_id=conversation_id,
                        score=score,
                        comment=comment,
                        idempotency_key=idempotency_key,
                        created_at=created_at,
                    ),
                    AuditEventModel(
                        tenant_id=tenant_id,
                        event_id=str(uuid4()),
                        event_type="conversation.satisfaction_submitted",
                        actor_subject_id="anonymous-widget-visitor",
                        correlation_id=idempotency_key,
                        resource_type="conversation",
                        resource_id=conversation_id,
                        details={"score": score, "has_comment": comment is not None},
                        created_at=created_at,
                    ),
                ]
            )
            await record_satisfaction_experience(
                session,
                tenant_id=tenant_id,
                site_id=site_id,
                conversation_id=conversation_id,
                rating_id=rating_id,
                score=score,
                occurred_at=created_at,
            )

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
    ) -> str:
        ticket_id = str(uuid5(NAMESPACE_URL, f"offline-ticket:{tenant_id}:{idempotency_key}"))
        async with self._session_factory.begin() as session:
            conversation = await session.scalar(
                select(ConversationModel)
                .where(
                    ConversationModel.tenant_id == tenant_id,
                    ConversationModel.conversation_id == conversation_id,
                )
                .with_for_update()
            )
            if conversation is not None:
                if conversation.site_id != site_id:
                    raise PermissionError("conversation is not available for this site")
                _require_visitor_session_access(conversation, visitor_session_id)
            existing = await session.scalar(
                select(SupportTicketModel).where(
                    SupportTicketModel.tenant_id == tenant_id,
                    SupportTicketModel.ticket_id == ticket_id,
                )
            )
            if existing is not None:
                if visitor_session_id is not None and (
                    conversation is None or existing.conversation_id != conversation_id
                ):
                    raise PermissionError("offline ticket does not match the visitor conversation")
                return ticket_id
            site = await session.scalar(
                select(SupportSiteModel).where(
                    SupportSiteModel.tenant_id == tenant_id,
                    SupportSiteModel.site_id == site_id,
                    SupportSiteModel.status == "active",
                )
            )
            if site is None:
                raise PermissionError("site is unavailable")
            queue_id = await _default_queue_id(session, tenant_id, site_id)
            if conversation is None:
                conversation = ConversationModel(
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    customer_id=None,
                    site_id=site_id,
                    visitor_session_id=visitor_session_id,
                    channel="web_widget",
                    status="waiting_human",
                    ownership_mode="queued",
                    assigned_agent_id=None,
                    queue_id=queue_id,
                    priority="normal",
                    tags=["offline-message"],
                    risk_level=0,
                    unread_count=1,
                    identity_verified=False,
                    last_message_at=created_at,
                    created_at=created_at,
                    updated_at=created_at,
                )
                session.add(conversation)
            else:
                conversation.status = "waiting_human"
                conversation.ownership_mode = "queued"
                conversation.assigned_agent_id = None
                conversation.queue_id = conversation.queue_id or queue_id
                conversation.tags = list(
                    dict.fromkeys([*(conversation.tags or []), "offline-message"])
                )
                conversation.unread_count += 1
                conversation.last_message_at = created_at
                conversation.updated_at = created_at
            session.add_all(
                [
                    MessageModel(
                        tenant_id=tenant_id,
                        message_id=str(
                            uuid5(NAMESPACE_URL, f"offline-message:{tenant_id}:{idempotency_key}")
                        ),
                        conversation_id=conversation_id,
                        role="user",
                        content=message,
                        redaction_status="applied",
                        message_type="chat",
                        author_subject_id=None,
                        message_metadata={"contact_email": email, "page_path": page_path},
                        created_at=created_at,
                    ),
                    SupportTicketModel(
                        tenant_id=tenant_id,
                        ticket_id=ticket_id,
                        customer_id=None,
                        conversation_id=conversation_id,
                        status="open",
                        subject="离线留言：" + message[:120],
                        source="offline_widget",
                        created_at=created_at,
                        updated_at=created_at,
                    ),
                    AuditEventModel(
                        tenant_id=tenant_id,
                        event_id=str(uuid4()),
                        event_type="offline_message.ticket_created",
                        actor_subject_id="anonymous-widget-visitor",
                        correlation_id=idempotency_key,
                        resource_type="support_ticket",
                        resource_id=ticket_id,
                        details={"site_id": site_id, "conversation_id": conversation_id},
                        created_at=created_at,
                    ),
                ]
            )
            await session.flush()
            return ticket_id

    async def list_knowledge_gaps(
        self, *, tenant_id: str, status: str | None, limit: int
    ) -> list[KnowledgeGap]:
        statement = select(KnowledgeGapModel).where(KnowledgeGapModel.tenant_id == tenant_id)
        if status is not None:
            statement = statement.where(KnowledgeGapModel.status == status)
        async with self._session_factory() as session:
            models = list(
                await session.scalars(
                    statement.order_by(KnowledgeGapModel.created_at.desc()).limit(limit)
                )
            )
        return [_gap(model) for model in models]

    async def create_knowledge_gap(
        self,
        *,
        gap: KnowledgeGap,
        correlation_id: str,
        idempotency_key: str,
    ) -> KnowledgeGap:
        operation = "knowledge_gap.created"
        async with self._session_factory.begin() as session:
            existing = await _operation(session, gap.tenant_id, idempotency_key)
            if existing is not None:
                model = await _knowledge_gap(session, gap.tenant_id, existing.resource_id)
                if model is None:
                    raise LookupError("knowledge gap was not found")
                return _gap(model)
            conversation = await session.scalar(
                select(ConversationModel).where(
                    ConversationModel.tenant_id == gap.tenant_id,
                    ConversationModel.conversation_id == gap.conversation_id,
                )
            )
            if conversation is None:
                raise LookupError("conversation was not found")
            model = KnowledgeGapModel(
                tenant_id=gap.tenant_id,
                gap_id=gap.gap_id,
                conversation_id=gap.conversation_id,
                source=gap.source,
                category=gap.category,
                summary=gap.summary,
                status=gap.status.value,
                created_by=gap.created_by,
                created_at=gap.created_at,
            )
            session.add(model)
            _record_write(
                session,
                tenant_id=gap.tenant_id,
                idempotency_key=idempotency_key,
                operation=operation,
                resource_type="knowledge_gap",
                resource_id=gap.gap_id,
                actor_subject_id=gap.created_by,
                correlation_id=correlation_id,
                details={"conversation_id": gap.conversation_id, "category": gap.category},
                occurred_at=gap.created_at,
            )
            await session.flush()
            return _gap(model)

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
    ) -> KnowledgeGap:
        operation = "knowledge_gap.resolved"
        async with self._session_factory.begin() as session:
            existing = await _operation(session, tenant_id, idempotency_key)
            if existing is not None:
                model = await _knowledge_gap(session, tenant_id, gap_id)
                if model is None:
                    raise LookupError("knowledge gap was not found")
                return _gap(model)
            model = await _knowledge_gap(session, tenant_id, gap_id, lock=True)
            if model is None:
                raise LookupError("knowledge gap was not found")
            model.status = KnowledgeGapStatus.RESOLVED.value
            model.resolved_by = actor_subject_id
            model.resolution_note = resolution_note
            model.resolved_at = resolved_at
            _record_write(
                session,
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                operation=operation,
                resource_type="knowledge_gap",
                resource_id=gap_id,
                actor_subject_id=actor_subject_id,
                correlation_id=correlation_id,
                details={"resolution_note": resolution_note},
                occurred_at=resolved_at,
            )
            await session.flush()
            return _gap(model)

    async def experience_summary(
        self, *, tenant_id: str, days: int, site_id: str | None
    ) -> CustomerExperienceSummary:
        since = datetime.now(UTC) - timedelta(days=days)
        satisfaction_filters = [
            SatisfactionRatingModel.tenant_id == tenant_id,
            SatisfactionRatingModel.created_at >= since,
        ]
        if site_id is not None:
            satisfaction_filters.append(SatisfactionRatingModel.site_id == site_id)
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(
                        func.count(SatisfactionRatingModel.id),
                        func.coalesce(func.avg(SatisfactionRatingModel.score), 0.0),
                    ).where(*satisfaction_filters)
                )
            ).one()
            gap_statement = select(func.count(KnowledgeGapModel.id)).where(
                KnowledgeGapModel.tenant_id == tenant_id,
                KnowledgeGapModel.status == KnowledgeGapStatus.OPEN.value,
            )
            if site_id is not None:
                gap_statement = gap_statement.join(
                    ConversationModel,
                    (ConversationModel.tenant_id == KnowledgeGapModel.tenant_id)
                    & (ConversationModel.conversation_id == KnowledgeGapModel.conversation_id),
                ).where(ConversationModel.site_id == site_id)
            open_gaps = await session.scalar(gap_statement)
            eligible_statement = select(func.count(ConversationModel.id)).where(
                ConversationModel.tenant_id == tenant_id,
                ConversationModel.status == "resolved",
                ConversationModel.resolved_at >= since,
            )
            if site_id is not None:
                eligible_statement = eligible_statement.where(ConversationModel.site_id == site_id)
            eligible = int(await session.scalar(eligible_statement) or 0)
        satisfaction_count = int(row[0])
        average_satisfaction = float(row[1])
        sample_target = 100
        return CustomerExperienceSummary(
            satisfaction_count,
            average_satisfaction,
            int(open_gaps or 0),
            eligible,
            satisfaction_count / eligible if eligible else 0.0,
            sample_target,
            satisfaction_count >= sample_target,
            satisfaction_count >= sample_target and average_satisfaction >= 4.5,
        )


async def _configuration_state(
    session: AsyncSession, tenant_id: str, site_id: str
) -> WidgetConfigurationState | None:
    site = await session.scalar(
        select(SupportSiteModel.id).where(
            SupportSiteModel.tenant_id == tenant_id, SupportSiteModel.site_id == site_id
        )
    )
    if site is None:
        return None
    models = list(
        await session.scalars(
            select(WidgetConfigVersionModel)
            .where(
                WidgetConfigVersionModel.tenant_id == tenant_id,
                WidgetConfigVersionModel.site_id == site_id,
            )
            .order_by(WidgetConfigVersionModel.version_number.desc())
        )
    )
    versions = tuple(_version(model) for model in models)
    return WidgetConfigurationState(
        site_id=site_id,
        published=next(
            (item for item in versions if item.status is WidgetConfigStatus.PUBLISHED), None
        ),
        draft=next((item for item in versions if item.status is WidgetConfigStatus.DRAFT), None),
        versions=versions,
    )


async def _replayed_state(
    session: AsyncSession,
    tenant_id: str,
    idempotency_key: str,
    operation: str,
    site_id: str,
) -> WidgetConfigurationState | None:
    existing = await _operation(session, tenant_id, idempotency_key)
    if existing is None:
        return None
    _assert_operation(existing, operation, site_id)
    state = await _configuration_state(session, tenant_id, site_id)
    if state is None:
        raise LookupError("site was not found")
    return state


async def _locked_site(
    session: AsyncSession, tenant_id: str, site_id: str
) -> SupportSiteModel | None:
    return await session.scalar(
        select(SupportSiteModel)
        .where(SupportSiteModel.tenant_id == tenant_id, SupportSiteModel.site_id == site_id)
        .with_for_update()
    )


async def _archive_published(
    session: AsyncSession, tenant_id: str, site_id: str, except_version_id: str | None = None
) -> None:
    models = list(
        await session.scalars(
            select(WidgetConfigVersionModel)
            .where(
                WidgetConfigVersionModel.tenant_id == tenant_id,
                WidgetConfigVersionModel.site_id == site_id,
                WidgetConfigVersionModel.status == WidgetConfigStatus.PUBLISHED.value,
            )
            .with_for_update()
        )
    )
    for model in models:
        if model.version_id != except_version_id:
            model.status = WidgetConfigStatus.ARCHIVED.value


async def _apply_actions(
    session: AsyncSession,
    conversation: ConversationModel,
    execution: AutomationExecution,
    actions: AutomationActions,
) -> None:
    now = execution.occurred_at
    if actions.queue_id:
        queue = await session.scalar(
            select(SupportQueueModel).where(
                SupportQueueModel.tenant_id == execution.tenant_id,
                SupportQueueModel.queue_id == actions.queue_id,
                SupportQueueModel.status == "active",
            )
        )
        if queue is None:
            raise LookupError("support queue was not found")
        if queue.site_id is not None and queue.site_id != conversation.site_id:
            raise LookupError("support queue is not available for this site")
        conversation.queue_id = actions.queue_id
    if actions.priority:
        conversation.priority = actions.priority
    if actions.tags:
        conversation.tags = list(dict.fromkeys([*(conversation.tags or []), *actions.tags]))
    if actions.direct_handoff:
        conversation.status = "waiting_human"
        conversation.ownership_mode = "queued"
        conversation.assigned_agent_id = None
        conversation.queue_id = conversation.queue_id or await _default_queue_id(
            session, execution.tenant_id, conversation.site_id
        )
        handoff = await session.scalar(
            select(HandoffRequestModel).where(
                HandoffRequestModel.tenant_id == execution.tenant_id,
                HandoffRequestModel.conversation_id == execution.conversation_id,
                HandoffRequestModel.status.in_(("pending", "assigned")),
            )
        )
        if handoff is None:
            session.add(
                HandoffRequestModel(
                    tenant_id=execution.tenant_id,
                    handoff_id=str(
                        uuid5(NAMESPACE_URL, f"automation-handoff:{execution.execution_id}")
                    ),
                    customer_id=conversation.customer_id,
                    conversation_id=execution.conversation_id,
                    reason_code="automation_rule",
                    risk_level=conversation.risk_level,
                    summary="确定性自动化规则触发人工接管。",
                    verified_evidence=[],
                    failed_tools=[],
                    knowledge_sources=[],
                    user_intent=None,
                    unresolved_question=None,
                    ai_attempt=None,
                    suggested_next_action="根据会话上下文回复访客",
                    reply_draft=None,
                    queue_id=conversation.queue_id,
                    status="pending",
                    priority=conversation.priority,
                    assigned_agent_id=None,
                    idempotency_key=sha256(execution.execution_id.encode()).hexdigest(),
                    trace_id=execution.execution_id,
                    created_at=now,
                    updated_at=now,
                )
            )
    if actions.create_ticket:
        ticket_id = str(uuid5(NAMESPACE_URL, f"automation-ticket:{execution.execution_id}"))
        existing_ticket = await session.scalar(
            select(SupportTicketModel.id).where(
                SupportTicketModel.tenant_id == execution.tenant_id,
                SupportTicketModel.ticket_id == ticket_id,
            )
        )
        if existing_ticket is None:
            session.add(
                SupportTicketModel(
                    tenant_id=execution.tenant_id,
                    ticket_id=ticket_id,
                    customer_id=conversation.customer_id,
                    conversation_id=execution.conversation_id,
                    status="open",
                    subject="自动化规则：" + execution.rule_id,
                    source="automation_rule",
                    created_at=now,
                    updated_at=now,
                )
            )
    conversation.updated_at = now


async def _default_queue_id(session: AsyncSession, tenant_id: str, site_id: str | None) -> str:
    queue = await session.scalar(
        select(SupportQueueModel)
        .where(
            SupportQueueModel.tenant_id == tenant_id,
            SupportQueueModel.status == "active",
            (SupportQueueModel.site_id == site_id) | SupportQueueModel.site_id.is_(None),
        )
        .order_by(
            (SupportQueueModel.site_id == site_id).desc(),
            SupportQueueModel.is_default.desc(),
            SupportQueueModel.name,
            SupportQueueModel.queue_id,
        )
    )
    if queue is None:
        raise LookupError("support queue was not found")
    return queue.queue_id


async def _automation_rule(
    session: AsyncSession, tenant_id: str, rule_id: str, lock: bool = False
) -> AutomationRuleModel | None:
    statement = select(AutomationRuleModel).where(
        AutomationRuleModel.tenant_id == tenant_id, AutomationRuleModel.rule_id == rule_id
    )
    return await session.scalar(statement.with_for_update() if lock else statement)


async def _knowledge_gap(
    session: AsyncSession, tenant_id: str, gap_id: str, lock: bool = False
) -> KnowledgeGapModel | None:
    statement = select(KnowledgeGapModel).where(
        KnowledgeGapModel.tenant_id == tenant_id, KnowledgeGapModel.gap_id == gap_id
    )
    return await session.scalar(statement.with_for_update() if lock else statement)


async def _operation(
    session: AsyncSession, tenant_id: str, idempotency_key: str
) -> SupportOperationRequestModel | None:
    return await session.scalar(
        select(SupportOperationRequestModel).where(
            SupportOperationRequestModel.tenant_id == tenant_id,
            SupportOperationRequestModel.idempotency_key == idempotency_key,
        )
    )


def _require_visitor_session_access(
    conversation: ConversationModel,
    visitor_session_id: str | None,
) -> None:
    if visitor_session_id is None:
        return
    if (
        conversation.customer_id is not None
        or conversation.visitor_session_id != visitor_session_id
    ):
        raise PermissionError("visitor session does not match the conversation")


def _record_write(
    session: AsyncSession,
    *,
    tenant_id: str,
    idempotency_key: str,
    operation: str,
    resource_type: str,
    resource_id: str,
    actor_subject_id: str,
    correlation_id: str,
    details: dict,
    occurred_at: datetime,
) -> None:
    records = [
        SupportOperationRequestModel(
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            operation=operation,
            resource_type=resource_type,
            resource_id=resource_id,
            response_snapshot=details,
            created_at=occurred_at,
        ),
        AuditEventModel(
            tenant_id=tenant_id,
            event_id=str(uuid4()),
            event_type=operation,
            actor_subject_id=actor_subject_id,
            correlation_id=correlation_id,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
            created_at=occurred_at,
        ),
    ]
    session.add_all(records)


def _assert_operation(
    existing: SupportOperationRequestModel, operation: str, resource_id: str
) -> None:
    if existing.operation != operation or existing.resource_id != resource_id:
        raise RuntimeError("idempotency key was already used for another operation")


def _config_payload(config: WidgetConfig) -> dict:
    payload = asdict(config)
    payload["holidays"] = list(config.holidays)
    return payload


def widget_config_from_payload(payload: dict) -> WidgetConfig:
    return WidgetConfig(
        welcome_message=str(payload.get("welcome_message", "")),
        online_message=str(payload.get("online_message", "")),
        offline_message=str(payload.get("offline_message", "")),
        business_timezone=str(payload.get("business_timezone", "Asia/Shanghai")),
        business_hours={
            str(key): str(value) for key, value in dict(payload.get("business_hours", {})).items()
        },
        holidays=tuple(str(value) for value in payload.get("holidays", [])),
        offline_form_enabled=bool(payload.get("offline_form_enabled", True)),
        primary_color=str(payload.get("primary_color", "#2563eb")),
        position=str(payload.get("position", "right")),
        agent_name=str(payload.get("agent_name", "在线客服")),
        agent_avatar_url=str(payload["agent_avatar_url"])
        if payload.get("agent_avatar_url")
        else None,
        mobile_enabled=bool(payload.get("mobile_enabled", True)),
        default_language=str(payload.get("default_language", "en")),
        handoff_timeout_seconds=int(payload.get("handoff_timeout_seconds", 120)),
        csat_enabled=bool(payload.get("csat_enabled", True)),
        customer_address_mode=str(payload.get("customer_address_mode", "neutral")),
        introduce_on_first_turn=bool(payload.get("introduce_on_first_turn", True)),
        launcher_asset_id=str(payload["launcher_asset_id"])
        if payload.get("launcher_asset_id")
        else None,
        launcher_image_fit=str(payload.get("launcher_image_fit", "contain")),
        agent_avatar_asset_id=str(payload["agent_avatar_asset_id"])
        if payload.get("agent_avatar_asset_id")
        else None,
    )


def _version(model: WidgetConfigVersionModel) -> WidgetConfigVersion:
    return WidgetConfigVersion(
        version_id=model.version_id,
        tenant_id=model.tenant_id,
        site_id=model.site_id,
        version_number=model.version_number,
        status=WidgetConfigStatus(model.status),
        config=widget_config_from_payload(model.config),
        created_by=model.created_by,
        created_at=model.created_at,
        published_at=model.published_at,
    )


def _actions_payload(actions: AutomationActions) -> dict:
    return {
        "queue_id": actions.queue_id,
        "priority": actions.priority,
        "tags": list(actions.tags),
        "create_ticket": actions.create_ticket,
        "direct_handoff": actions.direct_handoff,
    }


def _rule(model: AutomationRuleModel) -> AutomationRule:
    conditions = dict(model.conditions or {})
    actions = dict(model.actions or {})
    return AutomationRule(
        rule_id=model.rule_id,
        tenant_id=model.tenant_id,
        name=model.name,
        enabled=model.enabled,
        sort_order=model.sort_order,
        conditions=AutomationConditions(**conditions),
        actions=AutomationActions(
            queue_id=actions.get("queue_id"),
            priority=actions.get("priority"),
            tags=tuple(str(value) for value in actions.get("tags", [])),
            create_ticket=bool(actions.get("create_ticket", False)),
            direct_handoff=bool(actions.get("direct_handoff", False)),
        ),
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _execution(model: AutomationExecutionModel) -> AutomationExecution:
    return AutomationExecution(
        execution_id=model.execution_id,
        tenant_id=model.tenant_id,
        rule_id=model.rule_id,
        conversation_id=model.conversation_id,
        matched=model.matched,
        reasons=tuple(str(value) for value in model.reasons or []),
        actions_applied=tuple(str(value) for value in model.actions_applied or []),
        occurred_at=model.occurred_at,
    )


def _gap(model: KnowledgeGapModel) -> KnowledgeGap:
    return KnowledgeGap(
        gap_id=model.gap_id,
        tenant_id=model.tenant_id,
        conversation_id=model.conversation_id,
        source=model.source,
        category=model.category,
        summary=model.summary,
        status=KnowledgeGapStatus(model.status),
        created_by=model.created_by,
        created_at=model.created_at,
        resolved_by=model.resolved_by,
        resolution_note=model.resolution_note,
        resolved_at=model.resolved_at,
    )


def _within_business_hours(config: WidgetConfig | None, occurred_at: datetime) -> bool:
    if config is None:
        return True
    local = occurred_at.astimezone(ZoneInfo(config.business_timezone))
    if local.date().isoformat() in config.holidays:
        return False
    key = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[local.weekday()]
    value = config.business_hours.get(key)
    if value is None:
        return False
    start, end = value.split("-", 1)
    current = local.strftime("%H:%M")
    return start <= current < end
