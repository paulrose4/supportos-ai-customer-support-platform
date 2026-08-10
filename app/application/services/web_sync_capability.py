from dataclasses import dataclass
from datetime import UTC, datetime

from app.domain.models import AuthenticatedPrincipal
from app.domain.ports.web_sync_workers import WebSyncWorkerRegistryPort


@dataclass(frozen=True, slots=True)
class WebSyncWorkerCapability:
    status: str
    freshness: str
    coverage: str
    observed_at: datetime
    last_heartbeat_at: datetime | None
    heartbeat_expires_at: datetime | None
    covered_instance_count: int
    available_instance_count: int
    reason_codes: tuple[str, ...]

    @property
    def processing_ready(self) -> bool:
        return self.status == "healthy" and self.coverage == "covered"


class GetWebSyncCapabilityService:
    def __init__(self, registry: WebSyncWorkerRegistryPort) -> None:
        self._registry = registry

    async def execute(
        self,
        principal: AuthenticatedPrincipal,
        *,
        observed_at: datetime | None = None,
    ) -> WebSyncWorkerCapability:
        now = observed_at or datetime.now(UTC)
        try:
            registrations = await self._registry.list_current(
                tenant_id=principal.tenant_id,
                observed_at=now,
            )
        except Exception:  # noqa: BLE001
            return WebSyncWorkerCapability(
                status="unknown",
                freshness="unknown",
                coverage="unknown",
                observed_at=now,
                last_heartbeat_at=None,
                heartbeat_expires_at=None,
                covered_instance_count=0,
                available_instance_count=0,
                reason_codes=("worker_registry_unavailable",),
            )
        if not registrations:
            return WebSyncWorkerCapability(
                status="unavailable",
                freshness="expired",
                coverage="uncovered",
                observed_at=now,
                last_heartbeat_at=None,
                heartbeat_expires_at=None,
                covered_instance_count=0,
                available_instance_count=0,
                reason_codes=("worker_tenant_not_covered",),
            )
        healthy = tuple(
            registration
            for registration in registrations
            if registration.health_state == "healthy" and not registration.draining
        )
        available = tuple(
            registration
            for registration in healthy
            if registration.capacity_in_use < registration.capacity_total
        )
        last_seen_at = max(item.last_seen_at for item in registrations)
        expires_at = max(item.expires_at for item in registrations)
        if healthy:
            reasons: tuple[str, ...] = ()
            status = "healthy"
        elif all(item.draining for item in registrations):
            reasons = ("worker_draining",)
            status = "unavailable"
        else:
            reasons = ("worker_unhealthy",)
            status = "unavailable"
        return WebSyncWorkerCapability(
            status=status,
            freshness="fresh",
            coverage="covered",
            observed_at=now,
            last_heartbeat_at=last_seen_at,
            heartbeat_expires_at=expires_at,
            covered_instance_count=len(registrations),
            available_instance_count=len(available),
            reason_codes=reasons,
        )
