from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RetentionCounts:
    admin_sessions: int = 0
    expired_customer_memory: int = 0
    expired_memory_candidates: int = 0
    expired_resolution_episodes: int = 0
    support_operation_requests: int = 0
    audit_events: int = 0

    @property
    def total(self) -> int:
        return (
            self.admin_sessions
            + self.expired_customer_memory
            + self.expired_memory_candidates
            + self.expired_resolution_episodes
            + self.support_operation_requests
            + self.audit_events
        )
