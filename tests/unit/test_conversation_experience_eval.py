import json
from pathlib import Path

from evals.graders.conversation_experience import (
    build_experience_report,
    grade_conversation_case,
)


def test_experience_grader_accepts_direct_natural_follow_up() -> None:
    grade = grade_conversation_case(
        {
            "id": "natural",
            "turns": [
                {"role": "user", "content": "What is it made from?"},
                {"role": "assistant", "content": "It is made from TPE."},
                {"role": "user", "content": "And the weight?"},
                {"role": "assistant", "content": "It weighs 13 kg."},
            ],
            "direct_answer_terms": ["13 kg"],
        }
    )

    assert grade.passed


def test_experience_grader_rejects_repetition_and_unplanned_question() -> None:
    grade = grade_conversation_case(
        {
            "id": "mechanical",
            "turns": [
                {"role": "assistant", "content": "I can help you with that today."},
                {
                    "role": "assistant",
                    "content": "I can help you with that today. Would you like another option?",
                },
            ],
            "follow_up_allowed": False,
        }
    )

    assert not grade.phrase_repetition_free
    assert not grade.unnecessary_follow_up_free


def test_experience_report_applies_thresholds() -> None:
    grade = grade_conversation_case(
        {
            "id": "direct",
            "turns": [{"role": "assistant", "content": "The price is $279."}],
            "direct_answer_terms": ["$279"],
        }
    )
    report = build_experience_report([grade])

    assert report.passes({"case_pass_rate": 1.0})


def test_conversation_experience_dataset_passes_full_gate() -> None:
    root = Path(__file__).resolve().parents[2]
    cases = [
        json.loads(line)
        for line in (root / "evals" / "datasets" / "conversation_experience.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    thresholds = json.loads(
        (root / "evals" / "conversation-experience-gates.json").read_text(encoding="utf-8")
    )

    report = build_experience_report([grade_conversation_case(case) for case in cases])

    assert report.passes(thresholds)
