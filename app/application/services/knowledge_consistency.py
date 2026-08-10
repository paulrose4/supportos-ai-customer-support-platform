from datetime import UTC, datetime

from app.application.dto.knowledge_consistency import (
    AuditKnowledgeConsistencyQuery,
    KnowledgeConsistencyAuditResult,
)
from app.domain.ports import KnowledgeControlPlanePort, KnowledgeIndexerPort


class KnowledgeConsistencyAuditService:
    def __init__(
        self,
        *,
        control_plane: KnowledgeControlPlanePort,
        indexer: KnowledgeIndexerPort,
    ) -> None:
        self._control_plane = control_plane
        self._indexer = indexer

    async def execute(
        self,
        query: AuditKnowledgeConsistencyQuery,
    ) -> KnowledgeConsistencyAuditResult:
        control = await self._control_plane.audit_site_publication(
            tenant_id=query.tenant_id,
            site_id=query.site_id,
            baseline_publication_id=query.baseline_publication_id,
        )
        index = await self._indexer.audit_site_publication(
            tenant_id=query.tenant_id,
            site_id=query.site_id,
            target_version_ids=control.baseline_version_ids,
        )
        errors: list[str] = []
        warnings: list[str] = []

        if control.publication_state != "active":
            errors.append(f"site_publication_state_{control.publication_state}")
        if (
            control.baseline_publication_id is not None
            and control.publication_active_id != control.baseline_publication_id
        ):
            errors.append("site_active_publication_does_not_match_baseline")
        if control.baseline_publication_id is None:
            errors.append("no_successful_publication_baseline")
        if control.baseline_missing_version_ids:
            errors.append("baseline_references_missing_versions")
        if control.baseline_publication_id and control.baseline_manifest_count == 0:
            errors.append("baseline_has_no_indexed_manifest_chunks")
        if control.invalid_current_pointer_count:
            errors.append("current_pointer_references_ineligible_version")
        if control.discarded_current_pointer_count:
            errors.append("current_pointer_references_discarded_version")
        if control.failed_current_pointer_count:
            errors.append("current_pointer_references_failed_version")
        if control.orphan_current_pointer_count:
            warnings.append("tenant_contains_orphan_current_pointer")
        if len(control.current_snapshot_ids) > 1:
            warnings.append("current_publication_reuses_multiple_source_snapshots")
        if control.baseline_publication_id and set(control.current_version_ids) != set(
            control.baseline_version_ids
        ):
            errors.append("current_versions_do_not_match_publication_manifest")
        if control.current_manifest_count != control.baseline_manifest_count:
            errors.append("current_manifest_count_does_not_match_publication")
        if control.identifier_conflicts:
            errors.append("publication_contains_product_identifier_conflicts")

        if control.active_product_snapshot_count != 1:
            errors.append("site_requires_exactly_one_active_product_snapshot")
        if (
            control.baseline_publication_id
            and control.active_product_snapshot_id != control.baseline_publication_id
        ):
            errors.append("active_product_snapshot_does_not_match_publication")
        if control.baseline_publication_id and control.baseline_product_snapshot_status is None:
            errors.append("publication_product_snapshot_is_missing")

        if not index.collection_exists:
            errors.append("qdrant_collection_is_missing")
        else:
            if index.target_point_count != control.baseline_manifest_count:
                errors.append("qdrant_target_points_do_not_match_postgres_manifest")
            if index.target_active_point_count != index.target_point_count:
                errors.append("qdrant_publication_points_are_not_all_active")
            if index.active_point_count != control.baseline_manifest_count:
                errors.append("qdrant_active_points_do_not_match_publication")
            if set(index.active_version_ids) != set(control.baseline_version_ids):
                errors.append("qdrant_active_versions_do_not_match_publication")

        baseline_projection_complete = bool(
            control.baseline_publication_id
            and not control.baseline_missing_version_ids
            and control.baseline_manifest_count > 0
            and index.collection_exists
            and index.target_point_count == control.baseline_manifest_count
        )
        consistent = not errors
        return KnowledgeConsistencyAuditResult(
            evaluated_at=datetime.now(UTC).isoformat(),
            consistent=consistent,
            retrieval_ready=consistent and index.active_point_count > 0,
            baseline_projection_complete=baseline_projection_complete,
            errors=tuple(dict.fromkeys(errors)),
            warnings=tuple(dict.fromkeys(warnings)),
            control_plane=control,
            vector_index=index,
        )
