from redis.asyncio import Redis


class RedisDependency:
    def __init__(self, client: Redis) -> None:
        self.client = client

    async def check(self) -> None:
        if not await self.client.ping():
            raise RuntimeError("Redis ping failed")

    async def close(self) -> None:
        await self.client.aclose()
