import pytest

from app.domain.models import RiskLevel
from app.domain.rules import classify_order_handoff


@pytest.mark.parametrize(
    ("message", "reason_code", "priority", "risk_level"),
    (
        ("Where is my order ORDER-ABCD?", "order_tracking", "normal", RiskLevel.AUTHENTICATED_READ),
        (
            "I need to cancel my order",
            "order_cancellation",
            "urgent",
            RiskLevel.HIGH_IMPACT_REQUEST,
        ),
        ("我的收货地址填错了", "order_address_change", "urgent", RiskLevel.HIGH_IMPACT_REQUEST),
        (
            "My shipping address is wrong. Please change the address "
            "for order #ED-92841 immediately.",
            "order_address_change",
            "urgent",
            RiskLevel.HIGH_IMPACT_REQUEST,
        ),
        (
            "My card was charged twice",
            "order_payment_issue",
            "urgent",
            RiskLevel.HIGH_IMPACT_REQUEST,
        ),
        ("包裹里面少件了", "order_fulfillment_issue", "high", RiskLevel.AUTHENTICATED_READ),
        ("I want a refund", "order_refund", "high", RiskLevel.HIGH_IMPACT_REQUEST),
    ),
)
def test_order_request_is_deterministically_routed_to_human(
    message: str,
    reason_code: str,
    priority: str,
    risk_level: RiskLevel,
) -> None:
    decision = classify_order_handoff(message)

    assert decision is not None
    assert decision.reason_code == reason_code
    assert decision.priority == priority
    assert decision.risk_level is risk_level
    assert decision.queue_id == "orders"


def test_order_reference_is_an_unverified_routing_hint() -> None:
    decision = classify_order_handoff(
        "My shipping address is wrong. Please change it for order #ED-92841."
    )

    assert decision is not None
    assert decision.order_reference == "ED-92841"


@pytest.mark.parametrize(
    "message",
    (
        "What is your refund policy?",
        "How long does shipping take?",
        "你们的退换货政策是什么？",
        "支持什么支付方式？",
    ),
)
def test_public_policy_question_stays_eligible_for_ai(message: str) -> None:
    assert classify_order_handoff(message) is None
