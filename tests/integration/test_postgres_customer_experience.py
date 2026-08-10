import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from app.application.services import default_widget_config
from app.domain.models import (
    AutomationActions,
    AutomationConditions,
    AutomationExecution,
    AutomationRule,
    KnowledgeGap,
    KnowledgeGapStatus,
)
from app.integrations.postgres.customer_experience import PostgreSQLCustomerExperienceAdapter
from app.integrations.postgres.models import (
    AuditEventModel,
    AutomationExecutionModel,
    AutomationRuleModel,
    ConversationModel,
    HandoffRequestModel,
    KnowledgeGapModel,
    MessageModel,
    OutboxEventModel,
    SatisfactionRatingModel,
    SupportOperationRequestModel,
    SupportQueueModel,
    SupportSiteModel,
    SupportTicketModel,
    TenantModel,
    WidgetConfigVersionModel,
)
from app.integrations.postgres.session import DatabaseSessionManager

pytestmark = pytest.mark.integration

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/customer_agent",
)


@pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS") != "1",
    reason="set RUN_INTEGRATION_TESTS=1 with PostgreSQL running",
)
@pytest.mark.asyncio
async def test_customer_experience_workflows_are_versioned_idempotent_and_audited() -> None:
    suffix = uuid4().hex
    tenant_id = f"tenant-{suffix}"
    site_id = f"site-{suffix}"
    conversation_id = f"conversation-{suffix}"
    manager = DatabaseSessionManager(DATABASE_URL)
    adapter = PostgreSQLCustomerExperienceAdapter(manager.session_factory)
    now = datetime.now(UTC)
    try:
        async with manager.session_factory.begin() as session:
            session.add_all(
                [
                    TenantModel(
                        tenant_id=tenant_id,
                        name="Experience Test Workspace",
                        status="active",
                        created_at=now,
                        updated_at=now,
                    ),
                    SupportSiteModel(
                        tenant_id=tenant_id,
                        site_id=site_id,
                        public_widget_id=f"site_pub_{suffix}",
                        name="Experience Test",
                        base_url="https://example.com",
                        allowed_origins=["https://example.com"],
                        widget_daily_message_limit=500,
                        primary_language="zh-CN",
                        status="active",
                        created_at=now,
                        updated_at=now,
                    ),
                    SupportQueueModel(
                        tenant_id=tenant_id,
                        queue_id="general",
                        name="General",
                        description="Default",
                        is_default=True,
                        status="active",
                        created_at=now,
                        updated_at=now,
                    ),
                    ConversationModel(
                        tenant_id=tenant_id,
                        conversation_id=conversation_id,
                        site_id=site_id,
                        channel="web_widget",
                        status="resolved",
                        ownership_mode="human",
                        queue_id="general",
                        priority="normal",
                        tags=[],
                        risk_level=0,
                        unread_count=0,
                        identity_verified=False,
                        resolved_at=now,
                        created_at=now,
                        updated_at=now,
                    ),
                ]
            )

        state = await adapter.save_widget_draft(
            tenant_id=tenant_id,
            site_id=site_id,
            version_id=f"version-{suffix}",
            config=default_widget_config("zh-CN"),
            actor_subject_id="manager-1",
            correlation_id="correlation-1",
            idempotency_key=f"widget-draft-{suffix}",
            created_at=now,
        )
        assert state.draft is not None
        published = await adapter.publish_widget_version(
            tenant_id=tenant_id,
            site_id=site_id,
            version_id=state.draft.version_id,
            actor_subject_id="manager-1",
            correlation_id="correlation-2",
            idempotency_key=f"widget-publish-{suffix}",
            published_at=now,
        )
        assert published.published is not None
        assert published.published.version_number == 1

        rule = AutomationRule(
            rule_id=f"rule_{suffix}",
            tenant_id=tenant_id,
            name="Create priority ticket",
            enabled=True,
            sort_order=100,
            conditions=AutomationConditions(site_id=site_id),
            actions=AutomationActions(
                queue_id="general", priority="high", tags=("vip",), create_ticket=True
            ),
            created_at=now,
            updated_at=now,
        )
        await adapter.save_automation_rule(
            rule=rule,
            actor_subject_id="manager-1",
            correlation_id="correlation-3",
            idempotency_key=f"rule-save-{suffix}",
        )
        execution = AutomationExecution(
            execution_id=f"execution-{suffix}",
            tenant_id=tenant_id,
            rule_id=rule.rule_id,
            conversation_id=conversation_id,
            matched=True,
            reasons=("all_conditions_matched",),
            actions_applied=("assign_queue", "set_priority", "add_tags", "create_ticket"),
            occurred_at=now,
        )
        await adapter.record_automation_execution(
            execution=execution,
            actions=rule.actions,
            actor_subject_id="automation-engine",
            correlation_id="correlation-4",
            idempotency_key=f"rule-execution-{suffix}",
        )
        await adapter.submit_satisfaction(
            tenant_id=tenant_id,
            site_id=site_id,
            conversation_id=conversation_id,
            rating_id=f"rating-{suffix}",
            score=5,
            comment="Helpful",
            idempotency_key=f"rating-request-{suffix}",
            created_at=now,
        )
        handoff_execution = AutomationExecution(
            execution_id=f"handoff-execution-{suffix}",
            tenant_id=tenant_id,
            rule_id=rule.rule_id,
            conversation_id=conversation_id,
            matched=True,
            reasons=("all_conditions_matched",),
            actions_applied=("direct_handoff",),
            occurred_at=now,
        )
        await adapter.record_automation_execution(
            execution=handoff_execution,
            actions=AutomationActions(direct_handoff=True),
            actor_subject_id="automation-engine",
            correlation_id="correlation-4b",
            idempotency_key=f"handoff-execution-{suffix}",
        )
        await adapter.record_automation_execution(
            execution=execution,
            actions=rule.actions,
            actor_subject_id="automation-engine",
            correlation_id="correlation-4",
            idempotency_key=f"rule-execution-{suffix}",
        )

        gap = KnowledgeGap(
            gap_id=f"gap-{suffix}",
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            source="agent_feedback",
            category="missing_knowledge",
            summary="Missing delivery policy",
            status=KnowledgeGapStatus.OPEN,
            created_by="manager-1",
            created_at=now,
        )
        await adapter.create_knowledge_gap(
            gap=gap,
            correlation_id="correlation-5",
            idempotency_key=f"gap-create-{suffix}",
        )
        summary = await adapter.experience_summary(tenant_id=tenant_id, days=30, site_id=site_id)
        assert summary.satisfaction_count == 1
        assert summary.average_satisfaction == 5.0
        assert summary.open_knowledge_gaps == 1

        async with manager.session_factory() as session:
            conversation = await session.scalar(
                select(ConversationModel).where(
                    ConversationModel.tenant_id == tenant_id,
                    ConversationModel.conversation_id == conversation_id,
                )
            )
            assert conversation is not None
            assert conversation.priority == "high"
            assert conversation.tags == ["vip"]
            assert conversation.status == "waiting_human"
            assert conversation.ownership_mode == "queued"
            ticket_count = await session.scalar(
                select(func.count(SupportTicketModel.id)).where(
                    SupportTicketModel.tenant_id == tenant_id
                )
            )
            handoff_count = await session.scalar(
                select(func.count(HandoffRequestModel.id)).where(
                    HandoffRequestModel.tenant_id == tenant_id
                )
            )
            assert ticket_count == 1
            assert handoff_count == 1
            publish_event = await session.scalar(
                select(OutboxEventModel).where(
                    OutboxEventModel.tenant_id == tenant_id,
                    OutboxEventModel.event_type == "widget_config.published",
                )
            )
            assert publish_event is not None
            assert publish_event.payload["public_widget_id"] == f"site_pub_{suffix}"
            assert publish_event.payload["version_id"] == state.draft.version_id
            publish_event_count = await session.scalar(
                select(func.count(OutboxEventModel.id)).where(
                    OutboxEventModel.tenant_id == tenant_id,
                    OutboxEventModel.event_type == "widget_config.published",
                )
            )
            assert publish_event_count == 1
    finally:
        async with manager.session_factory.begin() as session:
            for model in (
                OutboxEventModel,
                AuditEventModel,
                SupportOperationRequestModel,
                AutomationExecutionModel,
                AutomationRuleModel,
                SatisfactionRatingModel,
                KnowledgeGapModel,
                HandoffRequestModel,
                SupportTicketModel,
                WidgetConfigVersionModel,
                ConversationModel,
                SupportQueueModel,
                SupportSiteModel,
                TenantModel,
            ):
                await session.execute(delete(model).where(model.tenant_id == tenant_id))
        await manager.dispose()


@pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS") != "1",
    reason="set RUN_INTEGRATION_TESTS=1 with PostgreSQL running",
)
@pytest.mark.asyncio
async def test_public_customer_experience_writes_are_bound_to_visitor_session() -> None:
    suffix = uuid4().hex
    tenant_id = f"tenant-{suffix}"
    site_id = f"site-{suffix}"
    resolved_conversation_id = f"resolved-{suffix}"
    offline_conversation_id = f"offline-{suffix}"
    owner_session_id = f"visitor-owner-{suffix}"
    other_session_id = f"visitor-other-{suffix}"
    manager = DatabaseSessionManager(DATABASE_URL)
    adapter = PostgreSQLCustomerExperienceAdapter(manager.session_factory)
    now = datetime.now(UTC)
    try:
        async with manager.session_factory.begin() as session:
            session.add_all(
                [
                    TenantModel(
                        tenant_id=tenant_id,
                        name="Public Customer Experience Ownership Test",
                        status="active",
                        created_at=now,
                        updated_at=now,
                    ),
                    SupportSiteModel(
                        tenant_id=tenant_id,
                        site_id=site_id,
                        public_widget_id=f"site_pub_{suffix}",
                        name="Ownership Test",
                        base_url="https://example.com",
                        allowed_origins=["https://example.com"],
                        widget_daily_message_limit=500,
                        primary_language="en",
                        status="active",
                        created_at=now,
                        updated_at=now,
                    ),
                    SupportQueueModel(
                        tenant_id=tenant_id,
                        queue_id="general",
                        name="General",
                        description="Default",
                        is_default=True,
                        status="active",
                        created_at=now,
                        updated_at=now,
                    ),
                    ConversationModel(
                        tenant_id=tenant_id,
                        conversation_id=resolved_conversation_id,
                        customer_id=None,
                        site_id=site_id,
                        visitor_session_id=owner_session_id,
                        channel="web_widget",
                        status="resolved",
                        ownership_mode="human",
                        queue_id="general",
                        priority="normal",
                        tags=[],
                        risk_level=0,
                        unread_count=0,
                        identity_verified=False,
                        resolved_at=now,
                        created_at=now,
                        updated_at=now,
                    ),
                ]
            )

        satisfaction_arguments = {
            "tenant_id": tenant_id,
            "site_id": site_id,
            "conversation_id": resolved_conversation_id,
            "rating_id": f"rating-{suffix}",
            "score": 5,
            "comment": "Helpful",
            "idempotency_key": f"rating-request-{suffix}",
            "created_at": now,
        }
        with pytest.raises(PermissionError, match="visitor session"):
            await adapter.submit_satisfaction(
                **satisfaction_arguments,
                visitor_session_id=other_session_id,
            )
        await adapter.submit_satisfaction(
            **satisfaction_arguments,
            visitor_session_id=owner_session_id,
        )
        with pytest.raises(PermissionError, match="visitor session"):
            await adapter.submit_satisfaction(
                **satisfaction_arguments,
                visitor_session_id=other_session_id,
            )

        offline_arguments = {
            "tenant_id": tenant_id,
            "site_id": site_id,
            "conversation_id": offline_conversation_id,
            "email": "visitor@example.com",
            "message": "Please contact me",
            "page_path": "/products/1",
            "idempotency_key": f"offline-request-{suffix}",
            "created_at": now,
        }
        ticket_id = await adapter.create_offline_ticket(
            **offline_arguments,
            visitor_session_id=owner_session_id,
        )
        with pytest.raises(PermissionError, match="visitor session"):
            await adapter.create_offline_ticket(
                **offline_arguments,
                visitor_session_id=other_session_id,
            )

        async with manager.session_factory() as session:
            offline_conversation = await session.scalar(
                select(ConversationModel).where(
                    ConversationModel.tenant_id == tenant_id,
                    ConversationModel.conversation_id == offline_conversation_id,
                )
            )
            ticket = await session.scalar(
                select(SupportTicketModel).where(
                    SupportTicketModel.tenant_id == tenant_id,
                    SupportTicketModel.ticket_id == ticket_id,
                )
            )
            assert offline_conversation is not None
            assert offline_conversation.visitor_session_id == owner_session_id
            assert ticket is not None
            assert ticket.conversation_id == offline_conversation_id
    finally:
        async with manager.session_factory.begin() as session:
            for model in (
                AuditEventModel,
                SatisfactionRatingModel,
                MessageModel,
                SupportTicketModel,
                ConversationModel,
                SupportQueueModel,
                SupportSiteModel,
                TenantModel,
            ):
                await session.execute(delete(model).where(model.tenant_id == tenant_id))
        await manager.dispose()
