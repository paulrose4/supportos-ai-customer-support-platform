from typing import Protocol


class DependencyHealthPort(Protocol):
    async def check(self) -> None: ...
