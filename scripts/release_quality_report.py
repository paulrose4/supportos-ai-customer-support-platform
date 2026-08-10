import argparse
import asyncio
import hashlib
import json
import os
import subprocess
from pathlib import Path

from app.config import get_settings
from app.integrations.postgres.analytics import PostgreSQLSupportAnalyticsAdapter
from app.integrations.postgres.customer_experience import PostgreSQLCustomerExperienceAdapter
from app.integrations.postgres.session import DatabaseSessionManager

ROOT = Path(__file__).resolve().parents[1]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate enterprise customer-support gates.")
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--site-id")
    parser.add_argument("--minimum-operational-samples", type=int, default=100)
    return parser


async def _run(args: argparse.Namespace) -> int:
    if not 1 <= args.days <= 365:
        raise ValueError("days must be between 1 and 365")
    if args.minimum_operational_samples < 20:
        raise ValueError("minimum operational samples must be at least 20")
    settings = get_settings()
    manager = DatabaseSessionManager(settings.database_url)
    try:
        analytics = await PostgreSQLSupportAnalyticsAdapter(manager.session_factory).overview(
            tenant_id=args.tenant_id,
            days=args.days,
            site_id=args.site_id,
        )
        experience = await PostgreSQLCustomerExperienceAdapter(
            manager.session_factory
        ).experience_summary(
            tenant_id=args.tenant_id,
            days=args.days,
            site_id=args.site_id,
        )
    finally:
        await manager.dispose()
    model_report = _load("evals/results/commitment_model_1000.summary.json")
    retrieval_report = _load("evals/results/retrieval_gate.summary.json")
    model_dataset_current = model_report.get("dataset_sha256") == _sha256(
        "evals/datasets/commitment_opportunities.jsonl"
    )
    retrieval_dataset_current = retrieval_report.get("dataset_sha256") == _sha256(
        "evals/datasets/retrieval_support.jsonl"
    )
    minimum = args.minimum_operational_samples
    commit_sha = os.getenv("GIT_COMMIT_SHA") or _git_commit_sha()
    working_tree_clean = _working_tree_clean()
    model_commit_current = model_report.get("git_commit_sha") == commit_sha
    retrieval_commit_current = retrieval_report.get("git_commit_sha") == commit_sha
    auto_rate = (
        analytics.auto_resolved_conversations / analytics.auto_resolution_eligible_conversations
        if analytics.auto_resolution_eligible_conversations
        else 0.0
    )
    handoff_rate = (
        analytics.complete_handoff_context_count / analytics.handoff_context_count
        if analytics.handoff_context_count
        else 0.0
    )
    auto_reopen_rate = (
        analytics.auto_resolution_reopened_conversations / analytics.auto_resolved_conversations
        if analytics.auto_resolved_conversations
        else 0.0
    )
    gates = {
        "commitment_error_rate": (
            model_dataset_current
            and model_commit_current
            and working_tree_clean
            and bool(model_report.get("release_confidence_met"))
        ),
        "recall_at_10": (
            retrieval_dataset_current
            and retrieval_commit_current
            and working_tree_clean
            and float(retrieval_report.get("score", 0.0)) >= 0.92
            and float(retrieval_report.get("identifier_score", 0.0)) >= 1.0
            and bool(retrieval_report.get("publication_ready"))
        ),
        "first_response_p95": (
            analytics.latency_sample_count >= minimum and analytics.first_response_p95_seconds < 3.0
        ),
        "auto_resolution_rate": (
            analytics.auto_resolution_eligible_conversations >= minimum and auto_rate >= 0.70
        ),
        "auto_resolution_reopen_rate": (
            analytics.auto_resolution_eligible_conversations >= minimum and auto_reopen_rate <= 0.05
        ),
        "high_risk_auto_resolution": analytics.high_risk_auto_resolved_conversations == 0,
        "csat": (experience.satisfaction_count >= 100 and experience.average_satisfaction >= 4.5),
        "handoff_context_completeness": (
            analytics.handoff_context_count >= 20 and handoff_rate >= 0.95
        ),
    }
    gate_status = {
        "commitment_error_rate": _artifact_status(
            gates["commitment_error_rate"],
            model_dataset_current,
            model_commit_current,
            working_tree_clean,
        ),
        "recall_at_10": _artifact_status(
            gates["recall_at_10"],
            retrieval_dataset_current,
            retrieval_commit_current,
            working_tree_clean,
        ),
        "first_response_p95": _sample_status(
            gates["first_response_p95"], analytics.latency_sample_count, minimum
        ),
        "auto_resolution_rate": _sample_status(
            gates["auto_resolution_rate"],
            analytics.auto_resolution_eligible_conversations,
            minimum,
        ),
        "auto_resolution_reopen_rate": _sample_status(
            gates["auto_resolution_reopen_rate"],
            analytics.auto_resolution_eligible_conversations,
            minimum,
        ),
        "high_risk_auto_resolution": ("passed" if gates["high_risk_auto_resolution"] else "failed"),
        "csat": _sample_status(gates["csat"], experience.satisfaction_count, 100),
        "handoff_context_completeness": _sample_status(
            gates["handoff_context_completeness"], analytics.handoff_context_count, 20
        ),
    }
    report = {
        "passed": all(gates.values()),
        "tenant_id": args.tenant_id,
        "days": args.days,
        "gates": gates,
        "gate_status": gate_status,
        "release_evidence": {
            "git_commit_sha": commit_sha,
            "working_tree_clean": working_tree_clean,
            "model_report_commit_current": model_commit_current,
            "retrieval_report_commit_current": retrieval_commit_current,
            "model_dataset_sha256": _sha256("evals/datasets/commitment_opportunities.jsonl"),
            "retrieval_dataset_sha256": _sha256("evals/datasets/retrieval_support.jsonl"),
            "qdrant_collection": settings.qdrant_collection,
            "embedding_model": (
                settings.openai_embedding_model
                if settings.embedding_provider == "openai"
                else settings.local_embedding_model
            ),
            "chat_model": settings.openai_chat_model,
            "knowledge_index_schema_version": settings.knowledge_index_schema_version,
        },
        "metrics": {
            "recall_at_10": retrieval_report.get("score", 0.0),
            "product_identifier_recall_at_10": retrieval_report.get("identifier_score", 0.0),
            "knowledge_publication_ready": bool(retrieval_report.get("publication_ready")),
            "commitment_error_rate": model_report.get("observed_error_rate", 1.0),
            "commitment_zero_error_upper_bound": model_report.get(
                "zero_error_95pct_upper_bound", 1.0
            ),
            "latency_samples": analytics.latency_sample_count,
            "first_response_p50_seconds": analytics.first_response_p50_seconds,
            "first_response_p95_seconds": analytics.first_response_p95_seconds,
            "first_response_p99_seconds": analytics.first_response_p99_seconds,
            "auto_resolution_eligible": analytics.auto_resolution_eligible_conversations,
            "auto_resolved": analytics.auto_resolved_conversations,
            "auto_resolution_rate": auto_rate,
            "auto_resolution_reopened": analytics.auto_resolution_reopened_conversations,
            "auto_resolution_reopen_rate": auto_reopen_rate,
            "high_risk_auto_resolved": analytics.high_risk_auto_resolved_conversations,
            "csat_count": experience.satisfaction_count,
            "average_csat": experience.average_satisfaction,
            "csat_response_rate": experience.satisfaction_response_rate,
            "handoff_context_count": analytics.handoff_context_count,
            "legacy_handoff_context_count": analytics.legacy_handoff_context_count,
            "handoff_context_completeness_rate": handoff_rate,
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


def _sample_status(passed: bool, count: int, required: int) -> str:
    if count < required:
        return "insufficient_samples"
    return "passed" if passed else "failed"


def _artifact_status(
    passed: bool,
    dataset_current: bool,
    commit_current: bool,
    working_tree_clean: bool,
) -> str:
    if not dataset_current:
        return "stale_dataset"
    if not commit_current:
        return "stale_commit"
    if not working_tree_clean:
        return "dirty_worktree"
    return "passed" if passed else "failed"


def _git_commit_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, timeout=5
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _working_tree_clean() -> bool:
    try:
        return not subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=ROOT, text=True, timeout=5
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return False


def _load(relative_path: str) -> dict:
    path = ROOT / relative_path
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(relative_path: str) -> str:
    return hashlib.sha256((ROOT / relative_path).read_bytes()).hexdigest()


def main() -> int:
    return asyncio.run(_run(_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
