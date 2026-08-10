from typing import Protocol, TypeVar

EntityT = TypeVar("EntityT")


class TenantRepositoryPort(Protocol[EntityT]):
    async def get(self, *, tenant_id: str, entity_id: str) -> EntityT | None: ...

    async def add(self, *, tenant_id: str, entity: EntityT) -> None: ...
