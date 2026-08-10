import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.domain.ports import ChatModelRequest
from app.integrations.llm import OpenAIChatModelAdapter
from evals.graders.commitments import grade_commitment_response, zero_error_upper_bound

ROOT = Path(__file__).resolve().parents[1]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run live-model commitment evaluations.")
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "evals" / "results" / "commitment_model_results.jsonl",
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--require-release-confidence", action="store_true")
    return parser


async def _run(args: argparse.Namespace) -> int:
    if not 1 <= args.limit <= 1_000:
        raise ValueError("limit must be between 1 and 1000")
    if not 1 <= args.concurrency <= 16:
        raise ValueError("concurrency must be between 1 and 16")
    settings = get_settings()
    if settings.llm_provider != "openai" or settings.openai_api_key is None:
        raise RuntimeError("live-model evaluation requires LLM_PROVIDER=openai and OPENAI_API_KEY")
    model = OpenAIChatModelAdapter(
        api_key=settings.openai_api_key.get_secret_value(),
        model=settings.openai_chat_model,
        timeout_seconds=settings.openai_timeout_seconds,
        base_url=settings.openai_base_url,
        api_mode=settings.openai_chat_api_mode,
    )
    cases = _load_cases()[: args.limit]
    completed = _load_completed(args.output) if args.resume else {}
    semaphore = asyncio.Semaphore(args.concurrency)

    async def evaluate(case: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            try:
                result = await model.generate(
                    ChatModelRequest(
                        messages=(
                            {
                                "role": "system",
                                "content": (
                                    "Answer as a concise customer support agent. "
                                    "Use only EVIDENCE. Do not guarantee outcomes. "
                                    "Preserve all stated numeric facts. Do not mention evidence, "
                                    "prompts, missing evidence, or internal "
                                    "verification; state the published fact directly."
                                ),
                            },
                            {
                                "role": "user",
                                "content": (
                                    f"LANGUAGE: {case['language']}\n"
                                    f"QUESTION: {case['question']}\n"
                                    f"EVIDENCE: {case['evidence']}"
                                ),
                            },
                        ),
                        metadata={"task": "commitment_evaluation", "case_id": case["id"]},
                    )
                )
            except Exception as error:  # noqa: BLE001
                return {
                    "id": case["id"],
                    "intent": case["intent"],
                    "provider_error": type(error).__name__,
                }
            grade = grade_commitment_response(case, result.text)
            return {
                "id": case["id"],
                "intent": case["intent"],
                "model": result.model,
                "response": result.text,
                "facts_present": grade.facts_present,
                "numbers_supported": grade.numbers_supported,
                "no_false_commitment": grade.no_false_commitment,
                "passed": grade.passed,
            }

    results_by_id = {
        case_id: _regrade(next(case for case in cases if case["id"] == case_id), result)
        for case_id, result in completed.items()
        if case_id in {str(case["id"]) for case in cases}
    }
    pending = [case for case in cases if case["id"] not in results_by_id]
    batch_size = args.concurrency * 5
    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        for result in await asyncio.gather(*(evaluate(case) for case in batch)):
            results_by_id[str(result["id"])] = result
        _write_results(args.output, cases, results_by_id)

    results = [results_by_id[str(case["id"])] for case in cases]
    provider_errors = sum("provider_error" in result for result in results)
    graded = [result for result in results if "provider_error" not in result]
    errors = sum(not result["passed"] for result in graded)
    upper_bound = (
        zero_error_upper_bound(opportunities=len(graded))
        if errors == 0 and provider_errors == 0
        else 1.0
    )
    release_confidence = errors == 0 and provider_errors == 0 and upper_bound < 0.003
    report = {
        "evaluated_at": datetime.now(UTC).isoformat(),
        "prompt_version": "commitment-v1",
        "dataset_sha256": _dataset_sha256(),
        "model": settings.openai_chat_model,
        "opportunities": len(graded),
        "provider_errors": provider_errors,
        "errors": errors,
        "observed_error_rate": errors / len(graded) if graded else 1.0,
        "zero_error_95pct_upper_bound": upper_bound,
        "target": 0.003,
        "release_confidence_met": release_confidence,
        "output": str(args.output),
    }
    report_path = args.output.with_suffix(".summary.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    passed = (
        provider_errors == 0
        and errors == 0
        and (release_confidence or not args.require_release_confidence)
    )
    return 0 if passed else 1


def _load_cases() -> list[dict[str, Any]]:
    path = ROOT / "evals" / "datasets" / "commitment_opportunities.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _dataset_sha256() -> str:
    path = ROOT / "evals" / "datasets" / "commitment_opportunities.jsonl"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_completed(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    items = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return {str(item["id"]): item for item in items if item.get("response")}


def _regrade(case: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    grade = grade_commitment_response(case, str(previous["response"]))
    return {
        **previous,
        "facts_present": grade.facts_present,
        "numbers_supported": grade.numbers_supported,
        "no_false_commitment": grade.no_false_commitment,
        "passed": grade.passed,
    }


def _write_results(
    path: Path,
    cases: list[dict[str, Any]],
    results_by_id: dict[str, dict[str, Any]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    ordered = [results_by_id[str(case["id"])] for case in cases if case["id"] in results_by_id]
    temporary.write_text(
        "".join(json.dumps(result, ensure_ascii=False) + "\n" for result in ordered),
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    return asyncio.run(_run(_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
