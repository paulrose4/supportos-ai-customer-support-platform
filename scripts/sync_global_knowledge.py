import argparse
import asyncio
import json
from uuid import uuid4

from app.application.tenant_context import global_knowledge_scope
from app.bootstrap.container import build_container
from app.config import get_settings
from app.domain.ports import GLOBAL_KNOWLEDGE_PARTITION


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a guarded, audited global knowledge synchronization."
    )
    parser.add_argument("--confirm-global-sync", action="store_true")
    parser.add_argument("--approval-reference", required=True)
    parser.add_argument("--actor-subject-id", required=True)
    parser.add_argument("--correlation-id")
    return parser


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    if not settings.platform_global_sync_enabled:
        raise PermissionError(
            "platform global synchronization is disabled; set "
            "PLATFORM_GLOBAL_SYNC_ENABLED=true for this one-shot operation"
        )
    if not args.confirm_global_sync:
        raise PermissionError("--confirm-global-sync is required")
    approval_reference = args.approval_reference.strip()
    actor_subject_id = args.actor_subject_id.strip()
    if not approval_reference or len(approval_reference) > 200:
        raise ValueError("approval reference must contain between 1 and 200 characters")
    if not actor_subject_id or len(actor_subject_id) > 100:
        raise ValueError("actor subject ID must contain between 1 and 100 characters")

    container = await build_container(settings)
    correlation_id = args.correlation_id or f"global-knowledge-cli-{uuid4()}"
    try:
        await container.knowledge_adapter.initialize()
        service = container.global_knowledge_sync_service
        if service is None:
            raise RuntimeError("global knowledge synchronization is unavailable")
        with global_knowledge_scope():
            report = await service.sync(
                GLOBAL_KNOWLEDGE_PARTITION,
                actor_subject_id=actor_subject_id,
                correlation_id=correlation_id,
                approval_reference=approval_reference,
            )
        print(
            json.dumps(
                {
                    "sync_job_id": report.sync_job_id,
                    "partition": GLOBAL_KNOWLEDGE_PARTITION,
                    "discovered_count": report.discovered_count,
                    "indexed_count": report.indexed_count,
                    "skipped_count": report.skipped_count,
                    "failed_count": report.failed_count,
                    "errors": report.errors,
                },
                ensure_ascii=False,
            )
        )
        return 0 if report.failed_count == 0 else 2
    finally:
        await container.knowledge_adapter.close()
        await container.database.dispose()


def main() -> int:
    return asyncio.run(_run(_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
