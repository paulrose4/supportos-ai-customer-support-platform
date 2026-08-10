import asyncio

from app.config import get_settings
from app.integrations.postgres.schema_contract import PostgreSQLKnowledgeSchemaDependency
from app.integrations.postgres.session import DatabaseSessionManager


async def _validate() -> None:
    settings = get_settings()
    database = DatabaseSessionManager(
        settings.migration_database_url or settings.database_url,
        pool_size=1,
        max_overflow=0,
    )
    try:
        await PostgreSQLKnowledgeSchemaDependency(database.engine).check()
    finally:
        await database.dispose()


def main() -> None:
    asyncio.run(_validate())
    print("knowledge database schema contract: ok")


if __name__ == "__main__":
    main()
