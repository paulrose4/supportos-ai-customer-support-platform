import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

from app.application.dto import ExperienceLearningCycleResult
from app.application.tenant_context import tenant_scope
from app.bootstrap.container import build_container
from app.config import get_settings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run tenant-scoped experience operations")
    parser.add_argument("--tenant-id", action="append", default=[])
    subparsers = parser.add_subparsers(dest="operation", required=True)

    cycle = subparsers.add_parser("cycle")
    cycle.add_argument("--minimum-cluster-size", type=int, default=3)
    cycle.add_argument("--minimum-eval-samples", type=int, default=30)

    serve = subparsers.add_parser("serve")
    serve.add_argument("--minimum-cluster-size", type=int, default=3)
    serve.add_argument("--minimum-eval-samples", type=int, default=30)
    serve.add_argument("--once", action="store_true")

    evaluation = subparsers.add_parser("record-eval")
    evaluation.add_argument("--report", type=Path, required=True)
    evaluation.add_argument("--dataset-version", required=True)
    evaluation.add_argument(
        "--evaluation-mode", choices=("offline", "shadow", "replay"), default="offline"
    )

    activate = subparsers.add_parser("activate")
    activate.add_argument("--approved-by", required=True)
    activate.add_argument("--rollout-percent", type=int, required=True)
    activate.add_argument("--include-memory-id", action="append", default=[])
    activate.add_argument("--exclude-memory-id", action="append", default=[])
    activate.add_argument("--eligible-intent", action="append", default=[])
    activate.add_argument("--eligible-site", action="append", default=[])
    activate.add_argument("--risk-ceiling", type=int, choices=(0, 1), default=1)
    activate.add_argument("--dataset-version", default="")

    rollback = subparsers.add_parser("rollback")
    rollback.add_argument("--actor-subject-id", required=True)

    deletion = subparsers.add_parser("delete-conversation")
    deletion.add_argument("--conversation-id", required=True)

    invalidation = subparsers.add_parser("invalidate")
    invalidation.add_argument("--dependency-kind", required=True)
    invalidation.add_argument("--dependency-value", action="append", required=True)
    return parser


async def _run(args: argparse.Namespace) -> dict[str, object]:
    settings = get_settings()
    tenant_ids = tuple(
        dict.fromkeys(
            value.strip()
            for value in (
                args.tenant_id
                or settings.tenant_experience_worker_tenant_ids
                or [settings.default_tenant_id]
            )
            if value.strip()
        )
    )
    if not tenant_ids:
        raise ValueError(
            "at least one trusted tenant ID is required via --tenant-id or "
            "TENANT_EXPERIENCE_WORKER_TENANT_IDS"
        )
    if args.operation != "serve" and len(tenant_ids) != 1:
        raise ValueError("one-shot experience operations require exactly one tenant ID")
    container = await build_container(settings)
    service = container.tenant_experience_learning_service
    try:
        if args.operation == "serve":
            last_results: dict[str, object] = {}
            while True:
                for tenant_id in tenant_ids:
                    with tenant_scope(tenant_id):
                        result = await service.run_cycle(
                            tenant_id=tenant_id,
                            release_version=settings.tenant_experience_release_version,
                            batch_size=settings.tenant_experience_worker_batch_size,
                            minimum_cluster_size=args.minimum_cluster_size,
                            minimum_eval_samples=args.minimum_eval_samples,
                            guardrail_minimum_samples=(
                                settings.tenant_experience_guardrail_minimum_samples
                            ),
                            maximum_negative_transfer_rate=(
                                settings.tenant_experience_maximum_negative_transfer_rate
                            ),
                            maximum_failure_rate_delta=(
                                settings.tenant_experience_maximum_failure_rate_delta
                            ),
                        )
                    last_results[tenant_id] = _cycle_payload(result)
                    if not args.once:
                        print(
                            json.dumps(
                                {
                                    "event": "tenant_experience_cycle_completed",
                                    **last_results[tenant_id],
                                },
                                ensure_ascii=False,
                            ),
                            flush=True,
                        )
                if args.once:
                    return {"tenants": last_results}
                await asyncio.sleep(settings.tenant_experience_worker_poll_seconds)

        tenant_id = tenant_ids[0]
        with tenant_scope(tenant_id):
            if args.operation == "cycle":
                result = await service.run_cycle(
                    tenant_id=tenant_id,
                    release_version=settings.tenant_experience_release_version,
                    batch_size=settings.tenant_experience_worker_batch_size,
                    minimum_cluster_size=args.minimum_cluster_size,
                    minimum_eval_samples=args.minimum_eval_samples,
                    guardrail_minimum_samples=(
                        settings.tenant_experience_guardrail_minimum_samples
                    ),
                    maximum_negative_transfer_rate=(
                        settings.tenant_experience_maximum_negative_transfer_rate
                    ),
                    maximum_failure_rate_delta=(
                        settings.tenant_experience_maximum_failure_rate_delta
                    ),
                )
                return _cycle_payload(result)
            if args.operation == "record-eval":
                payload = json.loads(args.report.read_text(encoding="utf-8"))
                metrics = payload.get("metrics")
                if not isinstance(metrics, dict):
                    raise ValueError("evaluation report metrics must be an object")
                await service.record_eval_run(
                    tenant_id=tenant_id,
                    eval_run_id=str(payload.get("eval_run_id") or uuid4()),
                    release_version=settings.tenant_experience_release_version,
                    evaluation_mode=args.evaluation_mode,
                    status=str(payload.get("status") or "completed"),
                    metrics=metrics,
                    sample_count=int(payload.get("sample_count") or 0),
                    dataset_version=args.dataset_version,
                )
                return {"recorded": True}
            if args.operation == "activate":
                count = await service.activate_release(
                    tenant_id=tenant_id,
                    release_version=settings.tenant_experience_release_version,
                    approved_by=args.approved_by,
                    rollout_percent=args.rollout_percent,
                    included_memory_ids=tuple(args.include_memory_id),
                    excluded_memory_ids=tuple(args.exclude_memory_id),
                    eligible_intents=tuple(args.eligible_intent),
                    eligible_sites=tuple(args.eligible_site),
                    risk_ceiling=args.risk_ceiling,
                    dataset_version=args.dataset_version,
                )
                return {"activated_memory_count": count}
            if args.operation == "delete-conversation":
                return await service.delete_conversation_experience(
                    tenant_id=tenant_id,
                    conversation_id=args.conversation_id,
                )
            if args.operation == "invalidate":
                return await service.invalidate_dependencies(
                    tenant_id=tenant_id,
                    dependency_kind=args.dependency_kind,
                    dependency_values=tuple(args.dependency_value),
                )
            count = await service.rollback_release(
                tenant_id=tenant_id,
                release_version=settings.tenant_experience_release_version,
                actor_subject_id=args.actor_subject_id,
            )
            return {"deactivated_memory_count": count}
    finally:
        await container.knowledge_adapter.close()
        if container.redis_backend is not None:
            await container.redis_backend.close()
        await container.database.dispose()


def _cycle_payload(result: ExperienceLearningCycleResult) -> dict[str, object]:
    return {
        "tenant_id": result.tenant_id,
        "release_version": result.release_version,
        "reconciled_outcomes": result.reconciled_outcomes,
        "guardrail_report": result.guardrail_report,
        "case_memories_built": result.case_memories_built,
        "patterns_consolidated": result.patterns_consolidated,
        "improvement_candidates_created": result.improvement_candidates_created,
        "gate_report": result.gate_report,
        "completed_at": result.completed_at.isoformat(),
    }


def main() -> None:
    args = _parser().parse_args()
    print(json.dumps(asyncio.run(_run(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
