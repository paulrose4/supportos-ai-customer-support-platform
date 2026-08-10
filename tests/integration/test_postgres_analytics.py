import os

import pytest

from app.integrations.postgres.analytics import PostgreSQLSupportAnalyticsAdapter
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


async def test_support_analytics_returns_percentiles_and_quality_denominators() -> None:
    manager = DatabaseSessionManager(DATABASE_URL)
    try:
        snapshot = await PostgreSQLSupportAnalyticsAdapter(manager.session_factory).overview(
            tenant_id="tenant-demo",
            days=365,
            site_id=None,
        )
    finally:
        await manager.dispose()

    assert snapshot.conversations >= 0
    assert snapshot.latency_sample_count >= 0
    assert snapshot.first_response_p95_seconds >= snapshot.first_response_p50_seconds
    assert snapshot.auto_resolution_eligible_conversations >= 0
    assert snapshot.complete_handoff_context_count <= snapshot.handoff_context_count
