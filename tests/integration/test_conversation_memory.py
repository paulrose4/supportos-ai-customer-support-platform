import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.domain.models import (
    AgentResponse,
    AuthenticatedPrincipal,
    ConversationWorkingMemory,
    ResponseKind,
    RiskLevel,
)
from app.integrations.postgres import (
    DatabaseSessionManager,
    PostgreSQLConversationPersistenceAdapter,
)
from app.integrations.postgres.models import (
    AuditEventModel,
    ConversationMemoryModel,
    ConversationModel,
    CustomerMemoryItemModel,
    CustomerModel,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION_TESTS") != "1",
        reason="set RUN_INTEGRATION_TESTS=1 with PostgreSQL running",
    ),
]

DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/customer_agent",
)


def _principal(
    tenant_id: str,
    subject_id: str = "anonymous-visitor",
    authentication_method: str = "public_widget_token",
    visitor_session_id: str | None = None,
) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        subject_id=subject_id,
        tenant_id=tenant_id,
        roles=frozenset({"customer"}),
        scopes=frozenset({"knowledge:read"}),
        authentication_method=authentication_method,
        authenticated_at=datetime.now(UTC),
        correlation_id=str(uuid4()),
        site_id="site-a",
        preferred_language="en",
        visitor_session_id=visitor_session_id,
    )


async def test_conversation_memory_summarizes_old_turns_and_is_idempotent() -> None:
    tenant_id = f"memory-test-{uuid4()}"
    conversation_id = str(uuid4())
    visitor_session_id = str(uuid4())
    principal = _principal(tenant_id, visitor_session_id=visitor_session_id)
    manager = DatabaseSessionManager(DATABASE_URL)
    adapter = PostgreSQLConversationPersistenceAdapter(manager.session_factory)
    last_trace_id = ""

    try:
        for index in range(1, 7):
            trace_id = str(uuid4())
            last_trace_id = trace_id
            response = AgentResponse(
                conversation_id=conversation_id,
                message=f"Answer for SKU-{index:03d}",
                kind=ResponseKind.ANSWER,
                risk_level=RiskLevel.PUBLIC,
                trace_id=trace_id,
                memory_state=ConversationWorkingMemory(
                    active_product_sku=f"SKU-{index:03d}",
                    candidate_product_skus=(f"SKU-{index:03d}", "SKU-ALT"),
                    candidate_product_labels=(f"Product {index}", "Alternative"),
                    country_code="US",
                    currency="USD",
                    confirmed_fields=("country", "currency", "product"),
                    missing_fields=("delivery_postcode",),
                    last_intent="product_lookup",
                    response_language="en",
                    revision=index,
                ),
            )
            await adapter.persist_exchange(
                principal=principal,
                user_message=f"Tell me about SKU-{index:03d}",
                response=response,
            )
            await adapter.save(
                principal=principal,
                conversation_id=conversation_id,
                working=response.memory_state,
                trace_id=trace_id,
            )

        context = await adapter.load(principal=principal, conversation_id=conversation_id)
        assert context.working.active_product_sku == "SKU-006"
        assert context.working.candidate_product_skus == ("SKU-006", "SKU-ALT")
        assert context.working.candidate_product_labels == ("Product 6", "Alternative")
        assert context.working.country_code == "US"
        assert context.working.currency == "USD"
        assert context.working.missing_fields == ("delivery_postcode",)
        assert context.summary.summarized_message_count == 8
        assert "SKU-001" in context.summary.discussed_product_skus
        assert "SKU-006" not in context.summary.discussed_product_skus
        assert context.durable_memories == ()

        await adapter.save(
            principal=principal,
            conversation_id=conversation_id,
            working=context.working,
            trace_id=last_trace_id,
        )
        updated = await adapter.load(principal=principal, conversation_id=conversation_id)
        assert updated.working.revision == context.working.revision

        other_tenant = await adapter.load(
            principal=_principal("other-tenant"),
            conversation_id=conversation_id,
        )
        assert other_tenant.working.active_product_sku is None

        async with manager.session() as session:
            memory = await session.scalar(
                select(ConversationMemoryModel).where(
                    ConversationMemoryModel.tenant_id == tenant_id,
                    ConversationMemoryModel.conversation_id == conversation_id,
                )
            )
            assert memory is not None
            assert memory.revision == 6
    finally:
        async with manager.session_factory.begin() as session:
            await session.execute(
                delete(AuditEventModel).where(
                    AuditEventModel.tenant_id == tenant_id,
                    AuditEventModel.resource_id == conversation_id,
                )
            )
            await session.execute(
                delete(ConversationModel).where(
                    ConversationModel.tenant_id == tenant_id,
                    ConversationModel.conversation_id == conversation_id,
                )
            )
        await manager.dispose()


async def test_conversation_memory_records_only_valid_used_memory_ids() -> None:
    tenant_id = f"memory-usage-test-{uuid4()}"
    customer_id = "customer-usage"
    conversation_id = str(uuid4())
    principal = _principal(tenant_id, customer_id, "mock")
    manager = DatabaseSessionManager(DATABASE_URL)
    adapter = PostgreSQLConversationPersistenceAdapter(manager.session_factory)
    now = datetime.now(UTC)

    try:
        async with manager.session_factory.begin() as session:
            session.add_all(
                [
                    CustomerModel(
                        tenant_id=tenant_id,
                        customer_id=customer_id,
                        display_name="Memory Usage Customer",
                        created_at=now,
                    ),
                    ConversationModel(
                        tenant_id=tenant_id,
                        conversation_id=conversation_id,
                        customer_id=customer_id,
                        site_id="site-a",
                        channel="wordpress_widget",
                        status="open",
                        ownership_mode="ai",
                        risk_level=0,
                        unread_count=0,
                        identity_verified=True,
                        created_at=now,
                        updated_at=now,
                    ),
                    CustomerMemoryItemModel(
                        tenant_id=tenant_id,
                        customer_id=customer_id,
                        memory_id="memory-usage-1",
                        kind="preference",
                        content="Prefers TPE",
                        source_type="customer",
                        source_id=conversation_id,
                        confidence=0.95,
                        consent_status="granted",
                        status="active",
                        expires_at=now.replace(year=now.year + 1),
                        created_at=now,
                        updated_at=now,
                    ),
                ]
            )

        context = await adapter.load(principal=principal, conversation_id=conversation_id)
        assert context.durable_memories[0].memory_id == "memory-usage-1"
        await adapter.save(
            principal=principal,
            conversation_id=conversation_id,
            working=ConversationWorkingMemory(revision=1),
            trace_id="trace-memory-usage",
            used_memory_ids=("memory-usage-1", "unknown-memory"),
        )

        async with manager.session_factory() as session:
            memory = await session.scalar(
                select(CustomerMemoryItemModel).where(
                    CustomerMemoryItemModel.tenant_id == tenant_id,
                    CustomerMemoryItemModel.memory_id == "memory-usage-1",
                )
            )
            usage_event = await session.scalar(
                select(AuditEventModel).where(
                    AuditEventModel.tenant_id == tenant_id,
                    AuditEventModel.event_type == "customer_memory.used",
                )
            )
        assert memory is not None
        assert memory.use_count == 1
        assert memory.last_used_at is not None
        assert usage_event is not None
        assert usage_event.details["memory_ids"] == ["memory-usage-1"]
    finally:
        async with manager.session_factory.begin() as session:
            for model in (
                CustomerMemoryItemModel,
                ConversationMemoryModel,
                AuditEventModel,
                ConversationModel,
                CustomerModel,
            ):
                await session.execute(delete(model).where(model.tenant_id == tenant_id))
        await manager.dispose()
