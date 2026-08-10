from datetime import UTC, datetime, timedelta
from inspect import signature

import pytest
from sqlalchemy.dialects import postgresql

from app.domain.models.web_sync_worker import WebSyncWorkerRegistration
from app.domain.ports.web_sync_workers import (
    ActiveTenantCatalogPort,
    WebSyncWorkerRegistryPort,
)
from app.integrations.postgres.models.web_sync_workers import WebSyncWorkerRegistrationModel
from app.integrations.postgres.web_sync_workers import PostgreSQLWebSyncWorkerRegistry


class _TransactionContext:
    def __init__(self, session: "_RecordingSession") -> None:
        self._session = session

    async def __aenter__(self) -> "_RecordingSession":
        return self._session

    async def __aexit__(self, *_args: object) -> None:
        return None


class _RecordingSession:
    def __init__(self, result: WebSyncWorkerRegistrationModel) -> None:
        self.result = result
        self.statements: list[object] = []

    async def scalar(self, statement: object) -> WebSyncWorkerRegistrationModel:
        self.statements.append(statement)
        return self.result


class _RecordingSessionFactory:
    def __init__(self, session: _RecordingSession) -> None:
        self.session = session

    def begin(self) -> _TransactionContext:
        return _TransactionContext(self.session)


def _registration(**overrides: object) -> WebSyncWorkerRegistration:
    now = datetime(2026, 8, 6, 12, tzinfo=UTC)
    values: dict[str, object] = {
        "tenant_id": "tenant-a",
        "worker_instance_id": "worker-a",
        "started_at": now,
        "last_seen_at": now,
        "expires_at": now + timedelta(seconds=30),
        "health_state": "healthy",
        "runtime_version": "test",
        "capacity_total": 4,
        "capacity_in_use": 1,
        "failure_codes": (" qdrant_unavailable ", "qdrant_unavailable", ""),
    }
    values.update(overrides)
    return WebSyncWorkerRegistration(**values)


def test_worker_registration_normalizes_failure_codes_and_rejects_bad_capacity() -> None:
    registration = _registration()
    assert registration.failure_codes == ("qdrant_unavailable",)

    with pytest.raises(ValueError, match="capacity_total"):
        _registration(capacity_total=0)
    with pytest.raises(ValueError, match="capacity_in_use"):
        _registration(capacity_in_use=5)


def test_registry_ports_are_narrow_and_tenant_scoped() -> None:
    assert set(ActiveTenantCatalogPort.__dict__) >= {"list_active_tenant_ids"}
    assert set(WebSyncWorkerRegistryPort.__dict__) >= {
        "register_or_heartbeat",
        "list_current",
        "unregister",
        "remove_expired",
    }
    assert "tenant_id" in signature(WebSyncWorkerRegistryPort.list_current).parameters
    assert "tenant_id" in signature(WebSyncWorkerRegistryPort.unregister).parameters


def test_worker_registration_model_has_tenant_key_and_capacity_guards() -> None:
    table = WebSyncWorkerRegistrationModel.__table__
    assert {
        "tenant_id",
        "worker_instance_id",
        "started_at",
        "last_seen_at",
        "expires_at",
        "health_state",
        "draining",
        "runtime_version",
        "capacity_total",
        "capacity_in_use",
        "failure_codes",
    }.issubset(table.columns.keys())
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if hasattr(constraint, "columns")
    }
    assert ("tenant_id", "worker_instance_id") in unique_columns
    checks = {
        str(constraint.sqltext)
        for constraint in table.constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }
    assert checks == {
        "capacity_total >= 1",
        "capacity_in_use >= 0 AND capacity_in_use <= capacity_total",
        "started_at <= last_seen_at",
        "expires_at > last_seen_at",
    }


def test_worker_registration_indexes_support_current_and_health_queries() -> None:
    indexes = {index.name: index for index in WebSyncWorkerRegistrationModel.__table__.indexes}
    assert tuple(indexes["ix_web_sync_worker_registrations_tenant_expiry"].columns.keys()) == (
        "tenant_id",
        "expires_at",
    )
    assert tuple(
        indexes["ix_web_sync_worker_registrations_tenant_health_expiry"].columns.keys()
    ) == ("tenant_id", "health_state", "draining", "expires_at")


async def test_heartbeat_upsert_rejects_out_of_order_state() -> None:
    registration = _registration(failure_codes=())
    model = WebSyncWorkerRegistrationModel(
        tenant_id=registration.tenant_id,
        worker_instance_id=registration.worker_instance_id,
        started_at=registration.started_at,
        last_seen_at=registration.last_seen_at,
        expires_at=registration.expires_at,
        health_state=registration.health_state,
        draining=registration.draining,
        runtime_version=registration.runtime_version,
        capacity_total=registration.capacity_total,
        capacity_in_use=registration.capacity_in_use,
        failure_codes=[],
    )
    session = _RecordingSession(model)
    registry = PostgreSQLWebSyncWorkerRegistry(_RecordingSessionFactory(session))  # type: ignore[arg-type]

    assert await registry.register_or_heartbeat(registration) == registration
    sql = str(session.statements[0].compile(dialect=postgresql.dialect()))  # type: ignore[union-attr]
    assert "ON CONFLICT (tenant_id, worker_instance_id) DO UPDATE" in sql
    assert "web_sync_worker_registrations.started_at < excluded.started_at" in sql
    assert "web_sync_worker_registrations.last_seen_at < excluded.last_seen_at" in sql
