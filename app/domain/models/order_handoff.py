from dataclasses import dataclass

from app.domain.models.chat import RiskLevel


@dataclass(frozen=True, slots=True)
class OrderHandoffDecision:
    reason_code: str
    user_intent: str
    priority: str
    risk_level: RiskLevel
    sla_minutes: int
    queue_id: str = "orders"
    sla_policy_version: str = "order-human-v1"
    order_reference: str | None = None
