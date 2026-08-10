from dataclasses import replace

from app.application.dto import AuditKnowledgeConsistencyQuery
from app.application.services import KnowledgeConsistencyAuditService
from app.domain.ports import (
    KnowledgeControlPlaneSiteAudit,
    KnowledgeIndexSiteAudit,
    ProductIdentifierConflict,
)


class _ControlPlane:
    def __init__(self, audit: KnowledgeControlPlaneSiteAudit) -> None:
        self.audit = audit

    async def audit_site_publication(self, **_values: object) -> KnowledgeControlPlaneSiteAudit:
        return self.audit


class _Index:
    def __init__(self, audit: KnowledgeIndexSiteAudit) -> None:
        self.audit = audit

    async def audit_site_publication(self, **_values: object) -> KnowledgeIndexSiteAudit:
        return self.audit


def _control() -> KnowledgeControlPlaneSiteAudit:
    return KnowledgeControlPlaneSiteAudit(
        tenant_id="tenant-a",
        site_id="site-a",
        baseline_publication_id="publication-a",
        baseline_version_ids=("version-a",),
        baseline_missing_version_ids=(),
        baseline_manifest_count=2,
        current_version_ids=("version-a",),
        current_manifest_count=2,
        current_document_count=1,
        invalid_current_pointer_count=0,
        discarded_current_pointer_count=0,
        failed_current_pointer_count=0,
        orphan_current_pointer_count=0,
        current_snapshot_ids=("publication-a",),
        active_product_snapshot_id="publication-a",
        active_product_snapshot_count=1,
        active_product_count=1,
        baseline_product_snapshot_status="active",
        baseline_product_count=1,
        identifier_conflicts=(),
        publication_active_id="publication-a",
    )


def _index() -> KnowledgeIndexSiteAudit:
    return KnowledgeIndexSiteAudit(
        collection_exists=True,
        site_point_count=4,
        active_point_count=2,
        inactive_point_count=2,
        target_point_count=2,
        target_active_point_count=2,
        active_version_ids=("version-a",),
    )


async def test_consistency_audit_accepts_aligned_publication() -> None:
    service = KnowledgeConsistencyAuditService(
        control_plane=_ControlPlane(_control()),  # type: ignore[arg-type]
        indexer=_Index(_index()),  # type: ignore[arg-type]
    )

    result = await service.execute(AuditKnowledgeConsistencyQuery("tenant-a", "site-a"))

    assert result.consistent is True
    assert result.retrieval_ready is True
    assert result.baseline_projection_complete is True
    assert result.errors == ()


async def test_consistency_audit_identifies_inactive_projection_and_state_drift() -> None:
    conflict = ProductIdentifierConflict(
        identifier="SKU-1",
        document_ids=("doc-a", "doc-b"),
        version_ids=("version-a", "version-b"),
        canonical_urls=("https://shop/a", "https://shop/b"),
        product_names=("Product A", "Product B"),
        brands=("Brand",),
    )
    control = replace(
        _control(),
        current_version_ids=("discarded-version",),
        invalid_current_pointer_count=1,
        discarded_current_pointer_count=1,
        active_product_snapshot_id="later-snapshot",
        identifier_conflicts=(conflict,),
    )
    index = replace(
        _index(),
        active_point_count=0,
        inactive_point_count=4,
        target_active_point_count=0,
        active_version_ids=(),
    )
    service = KnowledgeConsistencyAuditService(
        control_plane=_ControlPlane(control),  # type: ignore[arg-type]
        indexer=_Index(index),  # type: ignore[arg-type]
    )

    result = await service.execute(AuditKnowledgeConsistencyQuery("tenant-a", "site-a"))

    assert result.consistent is False
    assert result.retrieval_ready is False
    assert result.baseline_projection_complete is True
    assert "current_pointer_references_discarded_version" in result.errors
    assert "qdrant_publication_points_are_not_all_active" in result.errors
    assert "active_product_snapshot_does_not_match_publication" in result.errors
    assert "publication_contains_product_identifier_conflicts" in result.errors


async def test_consistency_audit_rejects_non_active_site_publication_state() -> None:
    control = replace(
        _control(),
        publication_state="recovery_required",
        publication_pending_id="publication-b",
        publication_error_code="rollback_failed:RuntimeError",
    )
    service = KnowledgeConsistencyAuditService(
        control_plane=_ControlPlane(control),  # type: ignore[arg-type]
        indexer=_Index(_index()),  # type: ignore[arg-type]
    )

    result = await service.execute(AuditKnowledgeConsistencyQuery("tenant-a", "site-a"))

    assert result.consistent is False
    assert result.retrieval_ready is False
    assert "site_publication_state_recovery_required" in result.errors


async def test_consistency_audit_rejects_missing_active_publication_pointer() -> None:
    service = KnowledgeConsistencyAuditService(
        control_plane=_ControlPlane(  # type: ignore[arg-type]
            replace(_control(), publication_active_id=None)
        ),
        indexer=_Index(_index()),  # type: ignore[arg-type]
    )

    result = await service.execute(AuditKnowledgeConsistencyQuery("tenant-a", "site-a"))

    assert result.consistent is False
    assert "site_active_publication_does_not_match_baseline" in result.errors
