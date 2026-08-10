import json
from datetime import UTC, datetime
from pathlib import Path

from app.domain.models import AuthenticatedPrincipal
from app.domain.rules import classify_care_risk, classify_minimum_risk, redact_sensitive_text
from evals.graders.metrics import recall

ROOT = Path(__file__).resolve().parents[2]


def _principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        subject_id="eval-customer",
        tenant_id="tenant-eval",
        roles=frozenset({"customer"}),
        scopes=frozenset({"orders:read:self", "tickets:read:self"}),
        authentication_method="mock",
        authenticated_at=datetime.now(UTC),
        correlation_id="eval-correlation",
    )


def _load(name: str) -> list[dict]:
    path = ROOT / "evals" / "datasets" / name
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_high_risk_routing_recall_is_one() -> None:
    cases = _load("routing.jsonl")
    expected = [case["expected_risk"] >= 2 for case in cases]
    predicted = [int(classify_minimum_risk(case["message"], _principal())) >= 2 for case in cases]

    assert recall(expected, predicted) == 1.0


def test_private_business_routing_recall_is_one() -> None:
    cases = [case for case in _load("routing.jsonl") if case["expected_risk"] == 1]

    assert cases
    assert all(int(classify_minimum_risk(case["message"], _principal())) == 1 for case in cases)


def test_redaction_dataset_has_zero_known_secret_leakage() -> None:
    for case in _load("redaction.jsonl"):
        output = redact_sensitive_text(case["input"])
        assert all(value not in output for value in case["must_not_contain"])
        assert all(value in output for value in case["must_contain"])


def test_care_safety_routing_matches_all_blocking_cases() -> None:
    for case in _load("care_safety.jsonl"):
        risk = classify_care_risk(case["message"])
        assert (risk.value if risk is not None else None) == case["expected_tier"]
