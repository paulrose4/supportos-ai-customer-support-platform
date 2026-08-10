import json
from pathlib import Path

from evals.graders.production import build_gate_report, grade_production_case

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    dataset_path = ROOT / "evals" / "datasets" / "production_support.jsonl"
    thresholds_path = ROOT / "evals" / "release-gates.json"
    cases = [
        json.loads(line)
        for line in dataset_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    thresholds = json.loads(thresholds_path.read_text(encoding="utf-8"))
    report = build_gate_report([grade_production_case(case) for case in cases])
    print(
        json.dumps(
            {
                "passed": report.passes(thresholds),
                "case_count": report.case_count,
                "passed_case_count": report.passed_case_count,
                "metrics": report.metrics,
                "hard_failures": report.hard_failures,
                "failed_case_ids": report.failed_case_ids,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report.passes(thresholds) else 1


if __name__ == "__main__":
    raise SystemExit(main())
