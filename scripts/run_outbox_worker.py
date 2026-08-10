import asyncio

from redis.asyncio import Redis

from app.config import get_settings
from app.integrations.cache import RedisPublishedWidgetCacheInvalidator
from app.integrations.postgres import DatabaseSessionManager, PostgreSQLOutboxDispatcher
from app.realtime import RedisRealtimeHub


async def _run() -> None:
    settings = get_settings()
    if settings.redis_url is None:
        raise RuntimeError("REDIS_URL is required for the outbox worker")
    database = DatabaseSessionManager(settings.database_url)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    dispatcher = PostgreSQLOutboxDispatcher(
        database.session_factory,
        RedisRealtimeHub(redis),
        published_widget_cache=RedisPublishedWidgetCacheInvalidator(redis),
    )
    try:
        while True:
            count = await dispatcher.dispatch_once()
            await asyncio.sleep(0.1 if count else 1.0)
    finally:
        await redis.aclose()
        await database.dispose()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
