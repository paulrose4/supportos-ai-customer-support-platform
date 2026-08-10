import json
from pathlib import Path

from evals.graders.conversation_experience import (
    build_experience_report,
    grade_conversation_case,
)

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    dataset_path = ROOT / "evals" / "datasets" / "conversation_experience.jsonl"
    thresholds_path = ROOT / "evals" / "conversation-experience-gates.json"
    cases = [
        json.loads(line)
        for line in dataset_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    thresholds = json.loads(thresholds_path.read_text(encoding="utf-8"))
    report = build_experience_report([grade_conversation_case(case) for case in cases])
    passed = report.passes(thresholds)
    print(
        json.dumps(
            {
                "passed": passed,
                "case_count": report.case_count,
                "passed_case_count": report.passed_case_count,
                "metrics": report.metrics,
                "failed_case_ids": report.failed_case_ids,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
