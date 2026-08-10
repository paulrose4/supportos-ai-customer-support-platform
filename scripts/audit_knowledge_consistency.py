import argparse
import asyncio
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.application.dto import AuditKnowledgeConsistencyQuery, KnowledgeConsistencyAuditResult
from app.bootstrap.container import build_container
from app.config import get_settings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only PostgreSQL/Qdrant knowledge publication consistency audit."
    )
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--site-id", required=True)
    parser.add_argument("--baseline-publication-id")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--include-version-ids", action="store_true")
    parser.add_argument("--max-conflicts", type=int, default=50)
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Always exit zero after producing the report.",
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    container = await build_container(get_settings())
    try:
        result = await container.knowledge_consistency_audit_service.execute(
            AuditKnowledgeConsistencyQuery(
                tenant_id=args.tenant_id,
                site_id=args.site_id,
                baseline_publication_id=args.baseline_publication_id,
            )
        )
        payload = _report_payload(
            result,
            include_version_ids=args.include_version_ids,
            max_conflicts=max(0, args.max_conflicts),
        )
        rendered = json.dumps(payload, ensure_ascii=False, indent=2)
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
        return 0 if args.report_only or result.consistent else 1
    finally:
        await container.knowledge_adapter.close()
        if container.product_recommendation_index is not None:
            await container.product_recommendation_index.close()
        if container.redis_backend is not None:
            await container.redis_backend.close()
        await container.database.dispose()


def _report_payload(
    result: KnowledgeConsistencyAuditResult,
    *,
    include_version_ids: bool,
    max_conflicts: int,
) -> dict[str, Any]:
    payload = {"mode": "read_only", **asdict(result)}
    control = payload["control_plane"]
    vector_index = payload["vector_index"]
    conflicts = control["identifier_conflicts"]
    control["identifier_conflict_count"] = len(conflicts)
    control["identifier_conflicts"] = conflicts[:max_conflicts]
    if not include_version_ids:
        for field_name in (
            "baseline_version_ids",
            "baseline_missing_version_ids",
            "current_version_ids",
        ):
            values = control.pop(field_name)
            control[f"{field_name}_summary"] = _identifier_summary(values)
        active_version_ids = vector_index.pop("active_version_ids")
        vector_index["active_version_ids_summary"] = _identifier_summary(active_version_ids)
    return payload


def _identifier_summary(values: list[str]) -> dict[str, object]:
    normalized = tuple(sorted(dict.fromkeys(values)))
    digest = hashlib.sha256("\n".join(normalized).encode()).hexdigest()
    return {"count": len(normalized), "sha256": digest}


def main() -> int:
    return asyncio.run(_run(_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
