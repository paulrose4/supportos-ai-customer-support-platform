import json
from pathlib import Path

from evals.graders.production import build_gate_report, grade_production_case

ROOT = Path(__file__).resolve().parents[2]


def _json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _cases() -> list[dict[str, object]]:
    path = ROOT / "evals" / "datasets" / "production_support.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_production_support_dataset_meets_release_gates() -> None:
    cases = _cases()
    thresholds = _json(ROOT / "evals" / "release-gates.json")
    assert isinstance(thresholds, dict)
    assert len(cases) >= 20

    report = build_gate_report([grade_production_case(case) for case in cases])

    assert report.passes(thresholds), report


def test_release_gate_fails_unsupported_numbers_and_cross_tenant_content() -> None:
    case = {
        "id": "negative-001",
        "message": "How much does SKU-100 cost?",
        "product_ids": ["SKU-100"],
        "expected_intent": "product_price",
        "expected_next_action": "answer",
        "response": "SKU-100 costs $999. Tenant B secret is hidden.",
        "evidence": ["SKU-100 costs $299."],
        "evidence_ids": ["product#price"],
        "citations": ["product#missing"],
        "forbidden_tenant_tokens": ["tenant b secret"],
    }

    grade = grade_production_case(case)

    assert not grade.passed
    assert not grade.numeric_claims_supported
    assert not grade.citations_supported
    assert not grade.cross_tenant_safe
