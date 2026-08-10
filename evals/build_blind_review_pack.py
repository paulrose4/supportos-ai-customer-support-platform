import argparse
import hashlib
import json
import re
from pathlib import Path
from random import Random
from typing import Any


def build_blind_review_pack(
    cases: list[dict[str, Any]],
    *,
    seed: int,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    randomizer = Random(seed)
    review_items: list[dict[str, Any]] = []
    answer_key: list[dict[str, str]] = []
    for case in cases:
        _require_redacted(case)
        case_id = _required(case, "id")
        baseline = _required(case, "baseline_response")
        candidate = _required(case, "candidate_response")
        candidate_first = bool(randomizer.getrandbits(1))
        option_a, option_b = (candidate, baseline) if candidate_first else (baseline, candidate)
        blind_id = hashlib.sha256(f"{seed}:{case_id}".encode()).hexdigest()[:16]
        review_items.append(
            {
                "blind_id": blind_id,
                "language": str(case.get("language") or "und"),
                "scenario": str(case.get("scenario") or "general"),
                "conversation": case.get("conversation") or [],
                "option_a": option_a,
                "option_b": option_b,
                "selection": "",
                "naturalness_1_to_5": None,
                "helpfulness_1_to_5": None,
                "notes": "",
            }
        )
        answer_key.append(
            {
                "blind_id": blind_id,
                "case_id": case_id,
                "candidate_option": "a" if candidate_first else "b",
            }
        )
    return review_items, answer_key


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a randomized customer-response blind review."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--answer-key", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260731)
    return parser


def main() -> int:
    args = _parser().parse_args()
    cases = _read_jsonl(args.input)
    review, answer_key = build_blind_review_pack(cases, seed=args.seed)
    _write_jsonl(args.output, review)
    _write_jsonl(args.answer_key, answer_key)
    return 0


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write_jsonl(path: Path, items: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in items),
        encoding="utf-8",
    )


def _required(case: dict[str, Any], field: str) -> str:
    value = str(case.get(field) or "").strip()
    if not value:
        raise ValueError(f"blind review field is required: {field}")
    return value


def _require_redacted(case: dict[str, Any]) -> None:
    serialized = json.dumps(case, ensure_ascii=False)
    sensitive_patterns = (
        r"(?i)(?<![\w.-])[\w.+-]+@[\w.-]+\.[a-z]{2,}(?![\w.-])",
        r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)",
        r"(?i)\bbearer\s+[a-z0-9._~+/=-]{8,}",
    )
    if any(re.search(pattern, serialized) for pattern in sensitive_patterns):
        raise ValueError("blind review input must be redacted before export")


if __name__ == "__main__":
    raise SystemExit(main())
