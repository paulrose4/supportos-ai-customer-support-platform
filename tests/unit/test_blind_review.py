import pytest

from evals.build_blind_review_pack import build_blind_review_pack
from evals.summarize_blind_review import summarize_blind_review


def test_blind_review_hides_candidate_identity_and_is_deterministic() -> None:
    cases = [
        {
            "id": "case-1",
            "language": "zh",
            "scenario": "price",
            "conversation": [{"role": "user", "content": "多少钱？"}],
            "baseline_response": "价格是 279 美元。需要我推荐吗？",
            "candidate_response": "价格是 279 美元。",
        }
    ]

    first_review, first_key = build_blind_review_pack(cases, seed=7)
    second_review, second_key = build_blind_review_pack(cases, seed=7)

    assert first_review == second_review
    assert first_key == second_key
    assert "candidate_option" not in first_review[0]
    assert first_key[0]["case_id"] == "case-1"


def test_blind_review_summary_calculates_candidate_win_rate() -> None:
    reviews = [
        {"blind_id": "one", "selection": "b", "language": "zh", "scenario": "price"},
        {"blind_id": "two", "selection": "tie", "language": "en", "scenario": "care"},
    ]
    keys = [
        {"blind_id": "one", "case_id": "1", "candidate_option": "b"},
        {"blind_id": "two", "case_id": "2", "candidate_option": "a"},
    ]

    report = summarize_blind_review(reviews, keys)

    assert report["candidate_win_rate"] == 1.0
    assert report["tie_rate"] == 0.5
    assert report["totals"]["invalid"] == 0


def test_blind_review_rejects_unredacted_pii() -> None:
    cases = [
        {
            "id": "case-sensitive",
            "conversation": [{"role": "user", "content": "Email me@example.com"}],
            "baseline_response": "Okay.",
            "candidate_response": "Understood.",
        }
    ]

    with pytest.raises(ValueError, match="must be redacted"):
        build_blind_review_pack(cases, seed=7)
