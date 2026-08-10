import os
from urllib.parse import urlparse

import pytest
from sqlalchemy.engine import make_url


@pytest.fixture(scope="session", autouse=True)
def require_isolated_integration_services() -> None:
    if os.getenv("RUN_INTEGRATION_TESTS") != "1":
        return

    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        raise pytest.UsageError("RUN_INTEGRATION_TESTS=1 requires an explicit TEST_DATABASE_URL")
    database_name = make_url(database_url).database or ""
    if database_name in {"customer_agent", "postgres", "template0", "template1"} or not (
        database_name.endswith("_test") or database_name.endswith("_integration")
    ):
        raise pytest.UsageError(
            "integration tests refuse non-isolated PostgreSQL database "
            f"{database_name!r}; use a name ending in _test or _integration"
        )

    redis_url = os.getenv("TEST_REDIS_URL")
    if redis_url:
        redis_path = urlparse(redis_url).path.strip("/") or "0"
        if redis_path == "0":
            raise pytest.UsageError("integration tests refuse Redis DB 0")
