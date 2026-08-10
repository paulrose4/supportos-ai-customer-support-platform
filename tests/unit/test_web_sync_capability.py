from datetime import UTC, datetime, timedelta

import pytest

from app.application.services.web_sync_capability import GetWebSyncCapabilityService
from app.domain.models import AuthenticatedPrincipal
from app.domain.models.web_sync_worker import WebSyncWorkerRegistration

OBSERVED_AT = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


class _Registry:
    def __init__(
        self,
        registrations: dict[str, tuple[WebSyncWorkerRegistration, ...]],
        *,
        error: Exception | None = None,
    ) -> None:
        self.registrations = registrations
        self.error = error
        self.calls: list[tuple[str, datetime]] = []

    async def list_current(
        self,
        *,
        tenant_id: str,
        observed_at: datetime,
    ) -> tuple[WebSyncWorkerRegistration, ...]:
        self.calls.append((tenant_id, observed_at))
        if self.error is not None:
            raise self.error
        return self.registrations.get(tenant_id, ())


def _principal(tenant_id: str) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        subject_id="admin-1",
        tenant_id=tenant_id,
        roles=frozenset({"admin"}),
        scopes=frozenset({"knowledge:sync"}),
        authentication_method="test",
        authenticated_at=OBSERVED_AT,
        correlation_id="corr-1",
    )


def _registration(
    tenant_id: str,
    *,
    health_state: str = "healthy",
    draining: bool = False,
    capacity_total: int = 1,
    capacity_in_use: int = 0,
    expires_at: datetime | None = None,
) -> WebSyncWorkerRegistration:
    started_at = OBSERVED_AT - timedelta(seconds=10)
    last_seen_at = OBSERVED_AT - timedelta(seconds=1)
    return WebSyncWorkerRegistration(
        tenant_id=tenant_id,
        worker_instance_id=f"worker-{tenant_id}",
        started_at=started_at,
        last_seen_at=last_seen_at,
        expires_at=expires_at or OBSERVED_AT + timedelta(seconds=30),
        health_state=health_state,
        draining=draining,
        capacity_total=capacity_total,
        capacity_in_use=capacity_in_use,
    )


async def test_worker_health_for_tenant_a_cannot_enable_tenant_b() -> None:
    registry = _Registry(
        {
            "tenant-a": (_registration("tenant-a"),),
            # No registration is returned for tenant B, despite tenant A being healthy.
        }
    )
    service = GetWebSyncCapabilityService(registry)  # type: ignore[arg-type]

    capability = await service.execute(_principal("tenant-b"), observed_at=OBSERVED_AT)

    assert registry.calls == [("tenant-b", OBSERVED_AT)]
    assert capability.status == "unavailable"
    assert capability.coverage == "uncovered"
    assert capability.processing_ready is False
    assert capability.reason_codes == ("worker_tenant_not_covered",)


async def test_busy_but_covered_worker_still_allows_queue_admission() -> None:
    registry = _Registry(
        {"tenant-a": (_registration("tenant-a", capacity_total=1, capacity_in_use=1),)}
    )
    service = GetWebSyncCapabilityService(registry)  # type: ignore[arg-type]

    capability = await service.execute(_principal("tenant-a"), observed_at=OBSERVED_AT)

    assert capability.status == "healthy"
    assert capability.coverage == "covered"
    assert capability.processing_ready is True
    assert capability.covered_instance_count == 1
    assert capability.available_instance_count == 0
    assert capability.reason_codes == ()


@pytest.mark.parametrize(
    ("registration", "expected_reasons"),
    [
        pytest.param(None, ("worker_tenant_not_covered",), id="expired"),
        pytest.param(
            _registration("tenant-a", health_state="unhealthy"),
            ("worker_unhealthy",),
            id="unhealthy",
        ),
        pytest.param(
            _registration("tenant-a", draining=True),
            ("worker_draining",),
            id="draining",
        ),
    ],
)
async def test_expired_unhealthy_or_draining_worker_fails_closed(
    registration: WebSyncWorkerRegistration | None,
    expected_reasons: tuple[str, ...],
) -> None:
    # list_current() is a current-record port: expired rows are filtered by the
    # adapter, so an empty result is the expired case at the application boundary.
    registrations = {} if registration is None else {"tenant-a": (registration,)}
    registry = _Registry(registrations)
    service = GetWebSyncCapabilityService(registry)  # type: ignore[arg-type]

    capability = await service.execute(_principal("tenant-a"), observed_at=OBSERVED_AT)

    assert capability.processing_ready is False
    assert capability.reason_codes == expected_reasons
    if registration is None:
        assert capability.status == "unavailable"
        assert capability.freshness == "expired"
        assert capability.coverage == "uncovered"
    else:
        assert capability.status == "unavailable"
        assert capability.freshness == "fresh"
        assert capability.coverage == "covered"


async def test_registry_failure_returns_unknown_and_fails_closed() -> None:
    registry = _Registry({}, error=ConnectionError("registry unavailable"))
    service = GetWebSyncCapabilityService(registry)  # type: ignore[arg-type]

    capability = await service.execute(_principal("tenant-a"), observed_at=OBSERVED_AT)

    assert capability.status == "unknown"
    assert capability.freshness == "unknown"
    assert capability.coverage == "unknown"
    assert capability.processing_ready is False
    assert capability.covered_instance_count == 0
    assert capability.available_instance_count == 0
    assert capability.reason_codes == ("worker_registry_unavailable",)
