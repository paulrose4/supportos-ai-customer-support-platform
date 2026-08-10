from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class WebSyncWorkerRegistration:
    """A worker's trusted, tenant-scoped liveness and capacity record."""

    tenant_id: str
    worker_instance_id: str
    started_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    health_state: str = "healthy"
    draining: bool = False
    runtime_version: str = "unknown"
    capacity_total: int = 1
    capacity_in_use: int = 0
    failure_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.tenant_id.strip():
            raise ValueError("tenant_id is required")
        if not self.worker_instance_id.strip():
            raise ValueError("worker_instance_id is required")
        if self.capacity_total < 1:
            raise ValueError("capacity_total must be at least one")
        if self.capacity_in_use < 0 or self.capacity_in_use > self.capacity_total:
            raise ValueError("capacity_in_use must be between zero and capacity_total")
        if self.started_at > self.last_seen_at:
            raise ValueError("started_at cannot be later than last_seen_at")
        if self.expires_at <= self.last_seen_at:
            raise ValueError("expires_at must be later than last_seen_at")

        # Keep failure metadata bounded and deterministic before it reaches JSON.
        normalized_codes = tuple(
            dict.fromkeys(code.strip() for code in self.failure_codes if code.strip())
        )
        if normalized_codes != self.failure_codes:
            object.__setattr__(self, "failure_codes", normalized_codes)
