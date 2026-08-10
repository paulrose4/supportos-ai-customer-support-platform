from datetime import datetime
from typing import Protocol

from app.domain.models.web_sync_worker import WebSyncWorkerRegistration


class ActiveTenantCatalogPort(Protocol):
    """Read-only control-plane view used to discover runtime tenants."""

    async def list_active_tenant_ids(self) -> tuple[str, ...]: ...


class WebSyncWorkerRegistryPort(Protocol):
    """Tenant-scoped worker liveness records.

    Implementations must keep every registry mutation and read behind the
    caller's tenant scope.  A worker instance can therefore register the same
    instance ID for multiple tenants without introducing a cross-tenant query.
    """

    async def register_or_heartbeat(
        self,
        registration: WebSyncWorkerRegistration,
    ) -> WebSyncWorkerRegistration: ...

    async def list_current(
        self,
        *,
        tenant_id: str,
        observed_at: datetime,
    ) -> tuple[WebSyncWorkerRegistration, ...]: ...

    async def unregister(
        self,
        *,
        tenant_id: str,
        worker_instance_id: str,
        started_at: datetime | None = None,
    ) -> bool: ...

    async def remove_expired(
        self,
        *,
        tenant_id: str,
        observed_at: datetime,
    ) -> int: ...
