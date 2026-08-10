from evals.generate_commitment_opportunities import build_cases
from evals.graders.commitments import grade_commitment_response, zero_error_upper_bound


def test_commitment_dataset_contains_one_thousand_opportunities() -> None:
    cases = build_cases()

    assert len(cases) == 1_000
    assert {case["intent"] for case in cases} == {
        "delivery_estimate",
        "product_price",
        "return_policy",
    }


def test_commitment_grader_rejects_unsupported_number_and_guarantee() -> None:
    case = build_cases()[0]
    grade = grade_commitment_response(
        case,
        "Processing takes 3-7 days and shipping takes 7-20 days. Guaranteed delivery in 9 days.",
    )

    assert not grade.passed
    assert not grade.numbers_supported
    assert not grade.no_false_commitment
    assert zero_error_upper_bound(opportunities=1_000) < 0.003
    assert zero_error_upper_bound(opportunities=300) > 0.003
