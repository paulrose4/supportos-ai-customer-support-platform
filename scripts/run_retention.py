import argparse
import asyncio
import json
from datetime import UTC, datetime
from uuid import uuid4

from app.application.dto import (
    ExecuteRetentionCommand,
    PreviewRetentionCommand,
    RetentionPolicy,
)
from app.application.services import RetentionService
from app.config import get_settings
from app.integrations.postgres import DatabaseSessionManager, PostgreSQLRetentionAdapter


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Preview or execute a tenant-scoped retention policy."
    )
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--approval-reference")
    parser.add_argument("--actor-subject-id")
    parser.add_argument("--run-key")
    return parser


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    policy = RetentionPolicy(
        policy_version=settings.retention_policy_version,
        admin_session_days=settings.retention_admin_session_days,
        expired_memory_grace_days=settings.retention_expired_memory_grace_days,
        support_operation_days=settings.retention_support_operation_days,
        audit_event_days=settings.retention_audit_event_days,
    )
    evaluated_at = datetime.now(UTC)
    manager = DatabaseSessionManager(settings.database_url)
    service = RetentionService(
        PostgreSQLRetentionAdapter(manager.session_factory),
        execution_enabled=settings.retention_execution_enabled,
    )
    try:
        if args.execute:
            if not args.approval_reference or not args.actor_subject_id or not args.run_key:
                raise ValueError(
                    "--execute requires --approval-reference, --actor-subject-id, and --run-key"
                )
            result = await service.execute(
                ExecuteRetentionCommand(
                    tenant_id=args.tenant_id,
                    policy=policy,
                    evaluated_at=evaluated_at,
                    run_key=args.run_key,
                    approval_reference=args.approval_reference,
                    actor_subject_id=args.actor_subject_id,
                    correlation_id=f"retention-cli-{uuid4()}",
                )
            )
        else:
            result = await service.preview(
                PreviewRetentionCommand(
                    tenant_id=args.tenant_id,
                    policy=policy,
                    evaluated_at=evaluated_at,
                )
            )
        print(
            json.dumps(
                {
                    "tenant_id": result.tenant_id,
                    "policy_version": result.policy_version,
                    "executed": result.executed,
                    "counts": {
                        "admin_sessions": result.counts.admin_sessions,
                        "expired_customer_memory": result.counts.expired_customer_memory,
                        "support_operation_requests": result.counts.support_operation_requests,
                        "audit_events": result.counts.audit_events,
                        "total": result.counts.total,
                    },
                },
                ensure_ascii=False,
            )
        )
        return 0
    finally:
        await manager.dispose()


def main() -> int:
    return asyncio.run(_run(_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
