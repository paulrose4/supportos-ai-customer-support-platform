from datetime import UTC, datetime

from app.domain.models import AuthenticatedPrincipal, RiskLevel
from app.domain.rules import classify_minimum_risk


def principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        subject_id="customer-1",
        tenant_id="tenant-a",
        roles=frozenset({"customer"}),
        scopes=frozenset({"orders:read:self"}),
        authentication_method="mock",
        authenticated_at=datetime.now(UTC),
        correlation_id="correlation-1",
    )


def test_high_impact_request_has_deterministic_risk_floor() -> None:
    assert classify_minimum_risk("我要退款", principal()) is RiskLevel.HIGH_IMPACT_REQUEST


def test_public_refund_policy_does_not_trigger_high_impact_floor() -> None:
    assert classify_minimum_risk("What is your refund policy?", principal()) is RiskLevel.PUBLIC


def test_cross_tenant_signal_is_severe() -> None:
    assert classify_minimum_risk("我要跨租户查询", principal()) is RiskLevel.SEVERE


def test_site_trust_question_is_not_misclassified_as_a_fraud_incident() -> None:
    assert classify_minimum_risk("Is this site legit or a scam?", principal()) is RiskLevel.PUBLIC
    assert classify_minimum_risk("My card was used for fraud", principal()) is RiskLevel.SEVERE
