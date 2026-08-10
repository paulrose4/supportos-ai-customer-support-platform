import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.application.tenant_context import tenant_scope
from app.integrations.postgres import DatabaseSessionManager
from app.integrations.postgres.models import AuditEventModel, OutboxEventModel

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


async def test_audit_event_and_outbox_commit_or_rollback_together() -> None:
    tenant_id = f"test-{uuid4()}"
    committed_id = str(uuid4())
    rolled_back_id = str(uuid4())
    manager = DatabaseSessionManager(DATABASE_URL)

    try:
        with tenant_scope(tenant_id):
            async with manager.session_factory.begin() as session:
                session.add(_audit(tenant_id, committed_id))

            with pytest.raises(RuntimeError, match="rollback"):
                async with manager.session_factory.begin() as session:
                    session.add(_audit(tenant_id, rolled_back_id))
                    await session.flush()
                    raise RuntimeError("rollback")

            async with manager.session_factory() as session:
                outbox_ids = tuple(
                    await session.scalars(
                        select(OutboxEventModel.event_id).where(
                            OutboxEventModel.tenant_id == tenant_id
                        )
                    )
                )

        assert committed_id in outbox_ids
        assert rolled_back_id not in outbox_ids
    finally:
        with tenant_scope(tenant_id):
            async with manager.session_factory.begin() as session:
                await session.execute(
                    delete(OutboxEventModel).where(OutboxEventModel.tenant_id == tenant_id)
                )
                await session.execute(
                    delete(AuditEventModel).where(AuditEventModel.tenant_id == tenant_id)
                )
        await manager.dispose()


def _audit(tenant_id: str, event_id: str) -> AuditEventModel:
    return AuditEventModel(
        tenant_id=tenant_id,
        event_id=event_id,
        event_type="conversation.tested",
        resource_type="conversation",
        resource_id="conversation-1",
        details={"status": "open"},
        created_at=datetime.now(UTC),
    )
