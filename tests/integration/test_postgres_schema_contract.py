import os

import pytest

from app.integrations.postgres import DatabaseSessionManager
from app.integrations.postgres.schema_contract import PostgreSQLKnowledgeSchemaDependency

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


async def test_knowledge_schema_contract_matches_runtime_expectations() -> None:
    manager = DatabaseSessionManager(DATABASE_URL)
    try:
        await PostgreSQLKnowledgeSchemaDependency(manager.engine).check()
    finally:
        await manager.dispose()
