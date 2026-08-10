import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def summarize_blind_review(
    reviews: list[dict[str, Any]],
    answer_key: list[dict[str, str]],
) -> dict[str, Any]:
    keys = {item["blind_id"]: item for item in answer_key}
    totals = {"candidate": 0, "baseline": 0, "tie": 0, "invalid": 0}
    strata: dict[str, dict[str, int]] = defaultdict(
        lambda: {"candidate": 0, "baseline": 0, "tie": 0, "invalid": 0}
    )
    scored = 0
    for review in reviews:
        key = keys.get(str(review.get("blind_id") or ""))
        selection = str(review.get("selection") or "").casefold()
        stratum = f"{review.get('language') or 'und'}:{review.get('scenario') or 'general'}"
        if key is None or selection not in {"a", "b", "tie"}:
            totals["invalid"] += 1
            strata[stratum]["invalid"] += 1
            continue
        scored += 1
        if selection == "tie":
            winner = "tie"
        elif selection == key["candidate_option"]:
            winner = "candidate"
        else:
            winner = "baseline"
        totals[winner] += 1
        strata[stratum][winner] += 1
    decisive = totals["candidate"] + totals["baseline"]
    return {
        "review_count": len(reviews),
        "scored_count": scored,
        "totals": totals,
        "candidate_win_rate": totals["candidate"] / decisive if decisive else 0.0,
        "tie_rate": totals["tie"] / scored if scored else 0.0,
        "strata": dict(sorted(strata.items())),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Summarize a customer-response blind review.")
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--answer-key", type=Path, required=True)
    parser.add_argument("--minimum-win-rate", type=float, default=0.60)
    return parser


def main() -> int:
    args = _parser().parse_args()
    report = summarize_blind_review(_read_jsonl(args.review), _read_jsonl(args.answer_key))
    report["passed"] = (
        report["totals"]["invalid"] == 0 and report["candidate_win_rate"] >= args.minimum_win_rate
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


if __name__ == "__main__":
    raise SystemExit(main())
