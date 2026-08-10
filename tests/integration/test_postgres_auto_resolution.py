import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.integrations.postgres.models import AuditEventModel, ConversationModel
from app.integrations.postgres.resolution import PostgreSQLAutoResolutionAdapter
from app.integrations.postgres.session import DatabaseSessionManager

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


async def test_due_ai_conversation_is_auto_resolved_with_audit() -> None:
    tenant_id = f"test-{uuid4()}"
    conversation_id = str(uuid4())
    now = datetime.now(UTC)
    manager = DatabaseSessionManager(DATABASE_URL)
    adapter = PostgreSQLAutoResolutionAdapter(manager.session_factory)
    try:
        async with manager.session_factory.begin() as session:
            session.add(
                ConversationModel(
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    site_id="site-a",
                    status="open",
                    ownership_mode="ai",
                    auto_resolution_eligible_at=now - timedelta(minutes=1),
                    created_at=now - timedelta(days=2),
                    updated_at=now - timedelta(minutes=1),
                )
            )

        assert await adapter.count_due(evaluated_at=now) == 1
        assert await adapter.resolve_due(evaluated_at=now, limit=10) == 1

        async with manager.session_factory() as session:
            conversation = await session.scalar(
                select(ConversationModel).where(
                    ConversationModel.tenant_id == tenant_id,
                    ConversationModel.conversation_id == conversation_id,
                )
            )
            assert conversation is not None
            assert conversation.status == "resolved"
            assert conversation.resolution_source == "auto_inactivity"
            audit = await session.scalar(
                select(AuditEventModel).where(AuditEventModel.tenant_id == tenant_id)
            )
            assert audit is not None
            assert audit.event_type == "conversation.auto_resolved"
    finally:
        async with manager.session_factory.begin() as session:
            await session.execute(
                delete(AuditEventModel).where(AuditEventModel.tenant_id == tenant_id)
            )
            await session.execute(
                delete(ConversationModel).where(ConversationModel.tenant_id == tenant_id)
            )
        await manager.dispose()
