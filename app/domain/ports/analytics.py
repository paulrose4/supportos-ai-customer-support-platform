from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RouteLatencySnapshot:
    route: str
    sample_count: int
    p50_seconds: float
    p95_seconds: float
    p99_seconds: float


@dataclass(frozen=True, slots=True)
class SupportAnalyticsSnapshot:
    days: int
    site_id: str | None
    conversations: int
    agent_runs: int
    ai_answers: int
    handoffs: int
    human_replied_conversations: int
    resolved_conversations: int
    average_first_response_seconds: float = 0.0
    average_human_response_seconds: float = 0.0
    average_resolution_seconds: float = 0.0
    unread_conversations: int = 0
    waiting_human_conversations: int = 0
    latency_sample_count: int = 0
    first_response_p50_seconds: float = 0.0
    first_response_p95_seconds: float = 0.0
    first_response_p99_seconds: float = 0.0
    latency_by_route: tuple[RouteLatencySnapshot, ...] = ()
    auto_resolution_eligible_conversations: int = 0
    auto_resolved_conversations: int = 0
    reopened_conversations: int = 0
    auto_resolution_reopened_conversations: int = 0
    high_risk_auto_resolved_conversations: int = 0
    handoff_context_count: int = 0
    complete_handoff_context_count: int = 0
    legacy_handoff_context_count: int = 0
    ai_eligible_runs: int = 0
    forced_order_handoffs: int = 0


class SupportAnalyticsPort(Protocol):
    async def overview(
        self, *, tenant_id: str, days: int, site_id: str | None
    ) -> SupportAnalyticsSnapshot: ...
