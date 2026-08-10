from pydantic import BaseModel


class RouteLatencyResponse(BaseModel):
    route: str
    sample_count: int
    p50_seconds: float
    p95_seconds: float
    p99_seconds: float


class SupportAnalyticsResponse(BaseModel):
    days: int
    site_id: str | None
    conversations: int
    agent_runs: int
    ai_answers: int
    handoffs: int
    human_replied_conversations: int
    resolved_conversations: int
    ai_answer_rate: float
    handoff_rate: float
    human_reply_rate: float
    resolution_rate: float
    average_first_response_seconds: float
    average_human_response_seconds: float
    average_resolution_seconds: float
    unread_conversations: int
    waiting_human_conversations: int
    latency_sample_count: int
    first_response_p50_seconds: float
    first_response_p95_seconds: float
    first_response_p99_seconds: float
    latency_by_route: list[RouteLatencyResponse]
    auto_resolution_eligible_conversations: int
    auto_resolved_conversations: int
    auto_resolution_rate: float
    reopened_conversations: int
    handoff_context_count: int
    complete_handoff_context_count: int
    handoff_context_completeness_rate: float
    legacy_handoff_context_count: int
    ai_eligible_runs: int
    forced_order_handoffs: int
    eligible_ai_answer_rate: float
