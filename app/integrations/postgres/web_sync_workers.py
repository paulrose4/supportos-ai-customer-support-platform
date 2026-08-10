from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.models.web_sync_worker import WebSyncWorkerRegistration
from app.domain.ports.web_sync_workers import (
    ActiveTenantCatalogPort,
    WebSyncWorkerRegistryPort,
)
from app.integrations.postgres.models.identity import TenantModel
from app.integrations.postgres.models.web_sync_workers import WebSyncWorkerRegistrationModel


class PostgreSQLActiveTenantCatalog(ActiveTenantCatalogPort):
    """Enumerate active tenants from the deliberately non-RLS control plane."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list_active_tenant_ids(self) -> tuple[str, ...]:
        async with self._session_factory() as session:
            values = await session.scalars(
                select(TenantModel.tenant_id)
                .where(TenantModel.status == "active")
                .order_by(TenantModel.tenant_id)
            )
            return tuple(str(value) for value in values)


class PostgreSQLWebSyncWorkerRegistry(WebSyncWorkerRegistryPort):
    """Persist worker coverage without weakening tenant RLS."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def register_or_heartbeat(
        self,
        registration: WebSyncWorkerRegistration,
    ) -> WebSyncWorkerRegistration:
        values = _model_values(registration)
        async with self._session_factory.begin() as session:
            statement = insert(WebSyncWorkerRegistrationModel).values(**values)
            statement = statement.on_conflict_do_update(
                index_elements=("tenant_id", "worker_instance_id"),
                set_={
                    "started_at": statement.excluded.started_at,
                    "last_seen_at": statement.excluded.last_seen_at,
                    "expires_at": statement.excluded.expires_at,
                    "health_state": statement.excluded.health_state,
                    "draining": statement.excluded.draining,
                    "runtime_version": statement.excluded.runtime_version,
                    "capacity_total": statement.excluded.capacity_total,
                    "capacity_in_use": statement.excluded.capacity_in_use,
                    "failure_codes": statement.excluded.failure_codes,
                },
                # A newer worker generation replaces an older process that
                # explicitly reused the same instance ID. Within one
                # generation, only a strictly newer heartbeat may update
                # health or capacity. An old process can therefore neither
                # overwrite nor unregister its replacement.
                where=(
                    (WebSyncWorkerRegistrationModel.started_at < statement.excluded.started_at)
                    | (
                        (WebSyncWorkerRegistrationModel.started_at == statement.excluded.started_at)
                        & (
                            WebSyncWorkerRegistrationModel.last_seen_at
                            < statement.excluded.last_seen_at
                        )
                    )
                ),
            ).returning(WebSyncWorkerRegistrationModel)
            model = await session.scalar(statement)
            if model is None:
                model = await session.scalar(
                    select(WebSyncWorkerRegistrationModel).where(
                        WebSyncWorkerRegistrationModel.tenant_id == registration.tenant_id,
                        WebSyncWorkerRegistrationModel.worker_instance_id
                        == registration.worker_instance_id,
                    )
                )
            if model is None:  # pragma: no cover - guarded by the unique upsert
                raise RuntimeError("worker registration disappeared during heartbeat")
            return _to_domain(model)

    async def list_current(
        self,
        *,
        tenant_id: str,
        observed_at: datetime,
    ) -> tuple[WebSyncWorkerRegistration, ...]:
        async with self._session_factory() as session:
            models = await session.scalars(
                select(WebSyncWorkerRegistrationModel)
                .where(
                    WebSyncWorkerRegistrationModel.tenant_id == tenant_id,
                    WebSyncWorkerRegistrationModel.expires_at > observed_at,
                )
                .order_by(WebSyncWorkerRegistrationModel.worker_instance_id)
            )
            return tuple(_to_domain(model) for model in models)

    async def unregister(
        self,
        *,
        tenant_id: str,
        worker_instance_id: str,
        started_at: datetime | None = None,
    ) -> bool:
        predicates = [
            WebSyncWorkerRegistrationModel.tenant_id == tenant_id,
            WebSyncWorkerRegistrationModel.worker_instance_id == worker_instance_id,
        ]
        if started_at is not None:
            predicates.append(WebSyncWorkerRegistrationModel.started_at == started_at)
        async with self._session_factory.begin() as session:
            result = await session.execute(
                delete(WebSyncWorkerRegistrationModel).where(*predicates)
            )
        return bool(result.rowcount)

    async def remove_expired(
        self,
        *,
        tenant_id: str,
        observed_at: datetime,
    ) -> int:
        async with self._session_factory.begin() as session:
            result = await session.execute(
                delete(WebSyncWorkerRegistrationModel).where(
                    WebSyncWorkerRegistrationModel.tenant_id == tenant_id,
                    WebSyncWorkerRegistrationModel.expires_at <= observed_at,
                )
            )
        return int(result.rowcount or 0)


def _model_values(registration: WebSyncWorkerRegistration) -> dict[str, object]:
    return {
        "tenant_id": registration.tenant_id,
        "worker_instance_id": registration.worker_instance_id,
        "started_at": registration.started_at,
        "last_seen_at": registration.last_seen_at,
        "expires_at": registration.expires_at,
        "health_state": registration.health_state,
        "draining": registration.draining,
        "runtime_version": registration.runtime_version,
        "capacity_total": registration.capacity_total,
        "capacity_in_use": registration.capacity_in_use,
        "failure_codes": list(registration.failure_codes),
    }


def _to_domain(model: WebSyncWorkerRegistrationModel) -> WebSyncWorkerRegistration:
    return WebSyncWorkerRegistration(
        tenant_id=model.tenant_id,
        worker_instance_id=model.worker_instance_id,
        started_at=model.started_at,
        last_seen_at=model.last_seen_at,
        expires_at=model.expires_at,
        health_state=model.health_state,
        draining=model.draining,
        runtime_version=model.runtime_version,
        capacity_total=model.capacity_total,
        capacity_in_use=model.capacity_in_use,
        failure_codes=tuple(str(code) for code in (model.failure_codes or ())),
    )
