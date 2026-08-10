import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from app.domain.models import ConversationStatus, MemoryKind, OwnershipMode
from app.integrations.postgres.models import (
    AuditEventModel,
    ConversationModel,
    CustomerMemoryItemModel,
    CustomerModel,
    MemoryCandidateModel,
    MessageModel,
    SupportOperationRequestModel,
)
from app.integrations.postgres.operations import PostgreSQLSupportOperationsAdapter
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
async def test_support_operations_are_transactional_idempotent_and_audited() -> None:
    suffix = uuid4().hex
    tenant_id = f"tenant-{suffix}"
    customer_id = f"customer-{suffix}"
    conversation_id = f"conversation-{suffix}"
    manager = DatabaseSessionManager(DATABASE_URL)
    adapter = PostgreSQLSupportOperationsAdapter(manager.session_factory)
    now = datetime.now(UTC)
    try:
        async with manager.session_factory.begin() as session:
            session.add_all(
                [
                    CustomerModel(
                        tenant_id=tenant_id,
                        customer_id=customer_id,
                        display_name="Integration Customer",
                        created_at=now,
                    ),
                    ConversationModel(
                        tenant_id=tenant_id,
                        conversation_id=conversation_id,
                        customer_id=customer_id,
                        channel="wordpress_widget",
                        status="waiting_human",
                        ownership_mode="queued",
                        risk_level=2,
                        unread_count=1,
                        identity_verified=True,
                        created_at=now,
                        updated_at=now,
                    ),
                ]
            )

        first = await adapter.transition_conversation(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            actor_subject_id="agent-1",
            correlation_id="correlation-1",
            target_status=ConversationStatus.OPEN,
            target_ownership=OwnershipMode.HUMAN,
            assigned_agent_id="agent-1",
            event_type="conversation.taken_over",
            idempotency_key="integration-takeover-key",
        )
        second = await adapter.transition_conversation(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            actor_subject_id="agent-1",
            correlation_id="correlation-1",
            target_status=ConversationStatus.OPEN,
            target_ownership=OwnershipMode.HUMAN,
            assigned_agent_id="agent-1",
            event_type="conversation.taken_over",
            idempotency_key="integration-takeover-key",
        )
        await adapter.append_agent_message(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            actor_subject_id="agent-1",
            correlation_id="correlation-2",
            content="Human reply",
            idempotency_key="integration-message-key",
        )
        await adapter.append_internal_note(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            actor_subject_id="agent-1",
            correlation_id="correlation-note",
            content="Operators only",
            idempotency_key="integration-note-key",
        )
        await adapter.append_internal_note(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            actor_subject_id="agent-1",
            correlation_id="correlation-note",
            content="Operators only",
            idempotency_key="integration-note-key",
        )
        memory = await adapter.upsert_customer_memory(
            tenant_id=tenant_id,
            customer_id=customer_id,
            actor_subject_id="agent-1",
            correlation_id="correlation-3",
            memory_id=f"memory-{suffix}",
            kind=MemoryKind.PREFERENCE,
            content="Prefers concise replies",
            source_type="agent",
            source_id=conversation_id,
            confidence=0.95,
            consent_status="granted",
            idempotency_key="integration-memory-key",
        )

        async with manager.session_factory() as session:
            operation_count = await session.scalar(
                select(func.count())
                .select_from(SupportOperationRequestModel)
                .where(SupportOperationRequestModel.tenant_id == tenant_id)
            )
            audit_count = await session.scalar(
                select(func.count())
                .select_from(AuditEventModel)
                .where(AuditEventModel.tenant_id == tenant_id)
            )
            message_count = await session.scalar(
                select(func.count())
                .select_from(MessageModel)
                .where(MessageModel.tenant_id == tenant_id)
            )
            note = await session.scalar(
                select(MessageModel).where(
                    MessageModel.tenant_id == tenant_id,
                    MessageModel.message_type == "internal_note",
                )
            )

        assert first.conversation.assigned_agent_id == "agent-1"
        assert second.conversation.assigned_agent_id == "agent-1"
        assert memory.customer_id == customer_id
        assert operation_count == 4
        assert audit_count == 4
        assert message_count == 2
        assert note is not None
        assert note.message_metadata == {"visibility": "operators_only"}
    finally:
        async with manager.session_factory.begin() as session:
            for model in (
                CustomerMemoryItemModel,
                MessageModel,
                SupportOperationRequestModel,
                AuditEventModel,
                ConversationModel,
                CustomerModel,
            ):
                await session.execute(delete(model).where(model.tenant_id == tenant_id))
        await manager.dispose()


@pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS") != "1",
    reason="set RUN_INTEGRATION_TESTS=1 with PostgreSQL running",
)
@pytest.mark.asyncio
async def test_concurrent_takeover_allows_only_one_agent() -> None:
    suffix = uuid4().hex
    tenant_id = f"tenant-concurrent-{suffix}"
    customer_id = f"customer-concurrent-{suffix}"
    conversation_id = f"conversation-concurrent-{suffix}"
    manager = DatabaseSessionManager(DATABASE_URL)
    adapter = PostgreSQLSupportOperationsAdapter(manager.session_factory)
    now = datetime.now(UTC)
    try:
        async with manager.session_factory.begin() as session:
            session.add_all(
                [
                    CustomerModel(
                        tenant_id=tenant_id,
                        customer_id=customer_id,
                        display_name="Concurrent Customer",
                        created_at=now,
                    ),
                    ConversationModel(
                        tenant_id=tenant_id,
                        conversation_id=conversation_id,
                        customer_id=customer_id,
                        channel="website_widget",
                        status="waiting_human",
                        ownership_mode="queued",
                        risk_level=1,
                        unread_count=1,
                        identity_verified=True,
                        created_at=now,
                        updated_at=now,
                    ),
                ]
            )

        async def takeover(agent_id: str) -> str:
            workspace = await adapter.transition_conversation(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                actor_subject_id=agent_id,
                correlation_id=f"correlation-{agent_id}",
                target_status=ConversationStatus.OPEN,
                target_ownership=OwnershipMode.HUMAN,
                assigned_agent_id=agent_id,
                event_type="conversation.taken_over",
                idempotency_key=f"takeover-{agent_id}",
            )
            return workspace.conversation.assigned_agent_id or ""

        results = await asyncio.gather(
            takeover("agent-a"),
            takeover("agent-b"),
            return_exceptions=True,
        )

        successes = [item for item in results if isinstance(item, str)]
        failures = [item for item in results if isinstance(item, RuntimeError)]
        assert len(successes) == 1
        assert len(failures) == 1
        assert successes[0] in {"agent-a", "agent-b"}
        assert "assigned to another agent" in str(failures[0])

        workspace = await adapter.get_workspace(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
        )
        assert workspace is not None
        assert workspace.conversation.assigned_agent_id == successes[0]
    finally:
        async with manager.session_factory.begin() as session:
            for model in (
                SupportOperationRequestModel,
                AuditEventModel,
                ConversationModel,
                CustomerModel,
            ):
                await session.execute(delete(model).where(model.tenant_id == tenant_id))
        await manager.dispose()


@pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS") != "1",
    reason="set RUN_INTEGRATION_TESTS=1 with PostgreSQL running",
)
@pytest.mark.asyncio
async def test_memory_candidate_lifecycle_is_idempotent_and_persists_expiry() -> None:
    suffix = uuid4().hex
    tenant_id = f"tenant-{suffix}"
    customer_id = f"customer-{suffix}"
    conversation_id = f"conversation-{suffix}"
    manager = DatabaseSessionManager(DATABASE_URL)
    adapter = PostgreSQLSupportOperationsAdapter(manager.session_factory)
    now = datetime.now(UTC)
    try:
        async with manager.session_factory.begin() as session:
            session.add_all(
                [
                    CustomerModel(
                        tenant_id=tenant_id,
                        customer_id=customer_id,
                        display_name="Memory V2 Customer",
                        created_at=now,
                    ),
                    ConversationModel(
                        tenant_id=tenant_id,
                        conversation_id=conversation_id,
                        customer_id=customer_id,
                        channel="wordpress_widget",
                        status="resolved",
                        ownership_mode="human",
                        risk_level=0,
                        unread_count=0,
                        identity_verified=True,
                        created_at=now,
                        updated_at=now,
                    ),
                ]
            )

        candidate_id = f"candidate-{suffix}"
        candidate_values = {
            "tenant_id": tenant_id,
            "customer_id": customer_id,
            "conversation_id": conversation_id,
            "actor_subject_id": "agent-1",
            "correlation_id": "correlation-memory-v2",
            "candidate_id": candidate_id,
            "candidate_fingerprint": uuid4().hex,
            "kind": MemoryKind.PREFERENCE,
            "normalized_value": {"preferred_material": "TPE"},
            "display_text": "Prefers TPE material",
            "source_message_ids": ("message-1",),
            "source_trace_id": "trace-memory-v2",
            "confidence": 0.98,
            "sensitivity": "standard",
            "consent_required": True,
            "idempotency_key": "integration-memory-candidate-propose",
        }
        proposed = await adapter.propose_memory_candidate(**candidate_values)
        repeated = await adapter.propose_memory_candidate(**candidate_values)
        approved, memory = await adapter.review_memory_candidate(
            tenant_id=tenant_id,
            customer_id=customer_id,
            candidate_id=candidate_id,
            actor_subject_id="agent-1",
            correlation_id="correlation-memory-v2-review",
            approve=True,
            rejection_reason=None,
            idempotency_key="integration-memory-candidate-approve",
        )
        approved_again, memory_again = await adapter.review_memory_candidate(
            tenant_id=tenant_id,
            customer_id=customer_id,
            candidate_id=candidate_id,
            actor_subject_id="agent-1",
            correlation_id="correlation-memory-v2-review",
            approve=True,
            rejection_reason=None,
            idempotency_key="integration-memory-candidate-approve",
        )

        assert proposed.candidate_id == repeated.candidate_id
        assert proposed.status.value == "awaiting_consent"
        assert approved.status.value == "approved"
        assert approved_again.status.value == "approved"
        assert memory is not None and memory_again is not None
        assert memory.normalized_value == {"preferred_material": "TPE"}
        assert memory.expires_at is not None
        assert memory.expires_at > now + timedelta(days=89)

        expired_candidate_id = f"candidate-expired-{suffix}"
        await adapter.propose_memory_candidate(
            **{
                **candidate_values,
                "candidate_id": expired_candidate_id,
                "candidate_fingerprint": uuid4().hex,
                "idempotency_key": "integration-memory-candidate-expired-propose",
            }
        )
        async with manager.session_factory.begin() as session:
            expired_model = await session.scalar(
                select(MemoryCandidateModel).where(
                    MemoryCandidateModel.tenant_id == tenant_id,
                    MemoryCandidateModel.candidate_id == expired_candidate_id,
                )
            )
            assert expired_model is not None
            expired_model.expires_at = now - timedelta(seconds=1)

        with pytest.raises(ValueError, match="has expired"):
            await adapter.review_memory_candidate(
                tenant_id=tenant_id,
                customer_id=customer_id,
                candidate_id=expired_candidate_id,
                actor_subject_id="agent-1",
                correlation_id="correlation-memory-v2-expired",
                approve=True,
                rejection_reason=None,
                idempotency_key="integration-memory-candidate-expired-review",
            )

        async with manager.session_factory() as session:
            expired_status = await session.scalar(
                select(MemoryCandidateModel.status).where(
                    MemoryCandidateModel.tenant_id == tenant_id,
                    MemoryCandidateModel.candidate_id == expired_candidate_id,
                )
            )
            expiry_operation = await session.scalar(
                select(SupportOperationRequestModel.operation).where(
                    SupportOperationRequestModel.tenant_id == tenant_id,
                    SupportOperationRequestModel.idempotency_key
                    == "integration-memory-candidate-expired-review",
                )
            )
        assert expired_status == "expired"
        assert expiry_operation == "memory_candidate.expired"
    finally:
        async with manager.session_factory.begin() as session:
            for model in (
                CustomerMemoryItemModel,
                MemoryCandidateModel,
                SupportOperationRequestModel,
                AuditEventModel,
                ConversationModel,
                CustomerModel,
            ):
                await session.execute(delete(model).where(model.tenant_id == tenant_id))
        await manager.dispose()
