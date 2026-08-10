import asyncio
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from app.application.dto import AuditKnowledgeConsistencyQuery
from app.bootstrap.container import build_container
from app.config import get_settings
from app.domain.ports import KnowledgeQuery
from app.domain.rules import product_reference_identifiers
from evals.graders.retrieval import (
    grade_identifier_retrieval_case,
    grade_retrieval_case,
    identifier_recall_at_k,
    recall_at_k,
)

ROOT = Path(__file__).resolve().parents[1]


async def _run() -> int:
    settings = get_settings()
    cases = [
        json.loads(line)
        for line in (ROOT / "evals" / "datasets" / "retrieval_support.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    container = await build_container(settings)
    await container.knowledge_adapter.initialize()
    document_grades = []
    identifier_grades = []
    case_diagnostics = []
    site_diagnostics: dict[str, dict[str, object]] = {}
    try:
        for case in cases:
            filters = dict(case.get("filters") or {})
            extracted_identifiers = product_reference_identifiers(str(case["query"]))
            if extracted_identifiers:
                filters["product_identifiers"] = extracted_identifiers
            evidence = await container.knowledge_adapter.search(
                KnowledgeQuery(
                    tenant_id=str(case["tenant_id"]),
                    text=str(case["query"]),
                    audience="public",
                    language=str(case.get("language") or "en"),
                    limit=10,
                    score_threshold=float(case.get("score_threshold", 0.0)),
                    filters=filters,
                )
            )
            document_grade = grade_retrieval_case(
                case_id=str(case["id"]),
                expected_document_ids=tuple(case["expected_document_ids"]),
                retrieved_document_ids=tuple(item.document_id for item in evidence),
                k=10,
            )
            document_grades.append(document_grade)
            expected_identifiers = tuple(case.get("expected_product_identifiers") or ())
            identifier_grade = None
            retrieved_identifiers = _evidence_product_identifiers(evidence)
            if expected_identifiers:
                identifier_grade = grade_identifier_retrieval_case(
                    case_id=str(case["id"]),
                    expected_identifiers=expected_identifiers,
                    retrieved_identifiers=retrieved_identifiers,
                    k=10,
                )
                identifier_grades.append(identifier_grade)
            if document_grade.recall < 1.0 or (
                identifier_grade is not None and identifier_grade.recall < 1.0
            ):
                case_diagnostics.append(
                    {
                        "case_id": str(case["id"]),
                        "document_recall": document_grade.recall,
                        "identifier_recall": (
                            None if identifier_grade is None else identifier_grade.recall
                        ),
                        "expected_document_ids": list(document_grade.expected_document_ids),
                        "retrieved_document_ids": list(document_grade.retrieved_document_ids),
                        "expected_product_identifiers": list(expected_identifiers),
                        "retrieved_product_identifiers": list(retrieved_identifiers),
                        "extracted_product_identifiers": list(extracted_identifiers),
                        "result_count": len(evidence),
                    }
                )

        site_keys = sorted(
            {
                (str(case["tenant_id"]), str((case.get("filters") or {}).get("site_id") or ""))
                for case in cases
                if (case.get("filters") or {}).get("site_id")
            }
        )
        for tenant_id, site_id in site_keys:
            audit = await container.knowledge_consistency_audit_service.execute(
                AuditKnowledgeConsistencyQuery(tenant_id=tenant_id, site_id=site_id)
            )
            site_diagnostics[f"{tenant_id}/{site_id}"] = {
                "consistent": audit.consistent,
                "retrieval_ready": audit.retrieval_ready,
                "publication_state": audit.control_plane.publication_state,
                "publication_active_id": audit.control_plane.publication_active_id,
                "publication_pending_id": audit.control_plane.publication_pending_id,
                "publication_error_code": audit.control_plane.publication_error_code,
                "baseline_publication_id": audit.control_plane.baseline_publication_id,
                "baseline_version_count": len(audit.control_plane.baseline_version_ids),
                "baseline_manifest_count": audit.control_plane.baseline_manifest_count,
                "invalid_current_pointer_count": (
                    audit.control_plane.invalid_current_pointer_count
                ),
                "product_identifier_conflict_count": len(audit.control_plane.identifier_conflicts),
                "qdrant_site_point_count": audit.vector_index.site_point_count,
                "qdrant_active_point_count": audit.vector_index.active_point_count,
                "qdrant_target_point_count": audit.vector_index.target_point_count,
                "errors": list(audit.errors),
            }
    finally:
        await container.knowledge_adapter.close()
        if container.product_recommendation_index is not None:
            await container.product_recommendation_index.close()
        if container.redis_backend is not None:
            await container.redis_backend.close()
        await container.database.dispose()
    document_grade_tuple = tuple(document_grades)
    identifier_grade_tuple = tuple(identifier_grades)
    document_score = recall_at_k(document_grade_tuple)
    identifier_score = identifier_recall_at_k(identifier_grade_tuple)
    publication_ready = bool(site_diagnostics) and all(
        bool(item["retrieval_ready"]) for item in site_diagnostics.values()
    )
    report = {
        "evaluated_at": datetime.now(UTC).isoformat(),
        "git_commit_sha": _git_commit_sha(),
        "dataset_sha256": hashlib.sha256(
            (ROOT / "evals" / "datasets" / "retrieval_support.jsonl").read_bytes()
        ).hexdigest(),
        "embedding_provider": settings.embedding_provider,
        "collection": settings.qdrant_collection,
        "query_path": "qdrant_hybrid_search_with_production_identifier_filters",
        "coverage": "retrieval_adapter_plus_publication_consistency_audit",
        "metric": "Document Recall@10",
        "case_count": len(document_grades),
        "score": round(document_score, 6),
        "threshold": 0.92,
        "identifier_metric": "Product Identifier Recall@10",
        "identifier_case_count": len(identifier_grades),
        "identifier_score": round(identifier_score, 6),
        "identifier_threshold": 1.0,
        "publication_ready": publication_ready,
        "passed": document_score >= 0.92 and identifier_score >= 1.0 and publication_ready,
        "failed_cases": [grade.case_id for grade in document_grades if grade.recall < 1.0],
        "failed_identifier_cases": [
            grade.case_id for grade in identifier_grades if grade.recall < 1.0
        ],
        "site_diagnostics": site_diagnostics,
        "case_diagnostics": case_diagnostics,
    }
    report_path = ROOT / "evals" / "results" / "retrieval_gate.summary.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


def _evidence_product_identifiers(evidence: list[object]) -> tuple[str, ...]:
    identifiers: list[str] = []
    for item in evidence:
        metadata = getattr(item, "metadata", {})
        product = metadata.get("product") if isinstance(metadata, dict) else None
        if not isinstance(product, dict):
            continue
        identifiers.extend(
            str(value).strip().upper()
            for value in (product.get("sku"), product.get("mpn"))
            if str(value or "").strip()
        )
    return tuple(dict.fromkeys(identifiers))


def _git_commit_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            text=True,
            timeout=5,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
