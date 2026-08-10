from datetime import UTC, datetime

from app.application.dto import GetSiteKnowledgeReadinessQuery
from app.application.services import SiteKnowledgeReadinessService
from app.domain.models import AuthenticatedPrincipal, ProductSnapshot
from app.domain.ports import KnowledgeChunkRecord, KnowledgeVersionDraft
from tests.fakes.adapters import InMemoryKnowledgeControlPlane, InMemoryProductCatalog


def _principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        subject_id="admin",
        tenant_id="tenant-a",
        roles=frozenset({"admin"}),
        scopes=frozenset({"knowledge:read"}),
        authentication_method="test",
        authenticated_at=datetime.now(UTC),
        correlation_id="corr-1",
    )


async def test_readiness_reports_missing_formal_catalog_without_shadow_substitution() -> None:
    control = InMemoryKnowledgeControlPlane()
    service = SiteKnowledgeReadinessService(
        catalog=InMemoryProductCatalog(),
        control_plane=control,
    )

    result = await service.get(GetSiteKnowledgeReadinessQuery(_principal(), "site-a"))

    assert not result.ready
    assert not result.catalog_ready
    assert "active_product_catalog_missing" in result.blocking_reasons


async def test_readiness_requires_catalog_policy_and_care_coverage() -> None:
    now = datetime.now(UTC)
    product = ProductSnapshot(
        tenant_id="tenant-a",
        site_id="site-a",
        snapshot_id="snapshot-1",
        product_key="SKU-1",
        sku="SKU-1",
        name="Product",
        canonical_url="https://shop.example/products/1",
        fetched_at=now,
        content_hash="hash",
        source_url="https://shop.example/products/1",
    )
    control = InMemoryKnowledgeControlPlane()
    for version_id, content_kind, topics in (("policy-v1", "general", ["policy", "privacy"]),):
        draft = KnowledgeVersionDraft(
            version_id=version_id,
            tenant_id="tenant-a",
            document_id=version_id,
            source_path=f"https://shop.example/{version_id}",
            title=version_id,
            version="1",
            content_hash=version_id,
            status="published",
            authority_level=80,
            priority=80,
            language="en",
            audience="public",
            effective_from=None,
            effective_to=None,
            metadata={
                "site_id": "site-a",
                "source_type": "website_html",
                "content_kind": content_kind,
                "topics": topics,
            },
            internal_links=(),
        )
        await control.stage_version(
            draft=draft,
            chunks=(KnowledgeChunkRecord("chunk", "hash", 0, None),),
        )
        await control.mark_indexed(
            tenant_id="tenant-a",
            document_id=version_id,
            version_id=version_id,
            point_ids=("point",),
            index_namespace="test",
        )
    care_draft = KnowledgeVersionDraft(
        version_id="care-v1",
        tenant_id="__global__",
        document_id="care-v1",
        source_path="care-v1.md",
        title="Approved care SOP",
        version="1",
        content_hash="care-v1",
        status="published",
        authority_level=90,
        priority=90,
        language="en",
        audience="public",
        effective_from=None,
        effective_to=None,
        metadata={
            "care_pack_id": "company-multi-material-care",
            "care_pack_version": "1.0.0",
            "care_scope": "company_global",
            "category": "product_care_sop",
            "approval_status": "approved",
            "approval_references": ["supplier-manual-care-rev-a"],
            "approved_steps": [
                {
                    "step_id": "care.tpe.keep-dry",
                    "instructions": {
                        "en": "Keep the product dry.",
                        "zh": "保持产品干燥。",
                    },
                }
            ],
        },
        internal_links=(),
    )
    await control.stage_version(
        draft=care_draft,
        chunks=(KnowledgeChunkRecord("care-chunk", "care-hash", 0, None),),
    )
    await control.mark_indexed(
        tenant_id="__global__",
        document_id="care-v1",
        version_id="care-v1",
        point_ids=("care-point",),
        index_namespace="test",
    )
    service = SiteKnowledgeReadinessService(
        catalog=InMemoryProductCatalog((product,)),
        control_plane=control,
    )

    result = await service.get(GetSiteKnowledgeReadinessQuery(_principal(), "site-a"))

    assert result.ready
    assert result.catalog_ready and result.policy_ready and result.care_ready
    assert result.active_product_count == 1
    assert result.active_document_count == 1
    assert result.blocking_reasons == ()


async def test_readiness_checks_site_language_against_global_care_pack() -> None:
    control = InMemoryKnowledgeControlPlane()
    care_draft = KnowledgeVersionDraft(
        version_id="care-de-v1",
        tenant_id="__global__",
        document_id="care-de-v1",
        source_path="care-de-v1.md",
        title="German care SOP",
        version="1",
        content_hash="care-de-v1",
        status="published",
        authority_level=90,
        priority=90,
        language="de-DE",
        audience="public",
        effective_from=None,
        effective_to=None,
        metadata={
            "care_pack_id": "company-multi-material-care",
            "care_pack_version": "1.0.0",
            "care_scope": "company_global",
            "category": "product_care_sop",
            "approval_status": "approved",
            "approval_references": ["supplier-manual-care-de"],
            "care_locales": ["de-DE"],
            "approved_steps": [
                {
                    "step_id": "care.tpe.keep-dry",
                    "instructions": {"de": "Halten Sie das Produkt trocken."},
                }
            ],
        },
        internal_links=(),
    )
    await control.stage_version(
        draft=care_draft,
        chunks=(KnowledgeChunkRecord("care-de-chunk", "care-de-hash", 0, None),),
    )
    await control.mark_indexed(
        tenant_id="__global__",
        document_id="care-de-v1",
        version_id="care-de-v1",
        point_ids=("care-de-point",),
        index_namespace="test",
    )
    service = SiteKnowledgeReadinessService(
        catalog=InMemoryProductCatalog(),
        control_plane=control,
    )

    german = await service.get(
        GetSiteKnowledgeReadinessQuery(_principal(), "site-a", language="de-DE")
    )
    english = await service.get(
        GetSiteKnowledgeReadinessQuery(_principal(), "site-a", language="en")
    )

    assert german.care_ready
    assert not english.care_ready
    assert "published_care_knowledge_missing" in english.blocking_reasons


async def test_readiness_does_not_treat_website_care_guide_as_approved_sop() -> None:
    control = InMemoryKnowledgeControlPlane()
    guide = KnowledgeVersionDraft(
        version_id="guide-v1",
        tenant_id="tenant-a",
        document_id="guide-v1",
        source_path="https://shop.example/care-guide",
        title="Website care guide",
        version="1",
        content_hash="guide-v1",
        status="published",
        authority_level=80,
        priority=80,
        language="en",
        audience="public",
        effective_from=None,
        effective_to=None,
        metadata={
            "site_id": "site-a",
            "source_type": "website_html",
            "content_kind": "guide",
            "content_topics": ["care", "storage"],
        },
        internal_links=(),
    )
    await control.stage_version(
        draft=guide,
        chunks=(KnowledgeChunkRecord("guide-chunk", "guide-hash", 0, None),),
    )
    await control.mark_indexed(
        tenant_id="tenant-a",
        document_id="guide-v1",
        version_id="guide-v1",
        point_ids=("guide-point",),
        index_namespace="test",
    )
    service = SiteKnowledgeReadinessService(
        catalog=InMemoryProductCatalog(),
        control_plane=control,
    )

    result = await service.get(GetSiteKnowledgeReadinessQuery(_principal(), "site-a"))

    assert not result.care_ready
    assert "published_care_knowledge_missing" in result.blocking_reasons


async def test_readiness_does_not_treat_pending_global_care_sop_as_ready() -> None:
    control = InMemoryKnowledgeControlPlane()
    draft = KnowledgeVersionDraft(
        version_id="care-review-v1",
        tenant_id="__global__",
        document_id="care-review-v1",
        source_path="care-sop-tpe-baseline-review.md",
        title="TPE care review draft",
        version="0.9.0",
        content_hash="care-review-v1",
        status="review",
        authority_level=90,
        priority=90,
        language="en",
        audience="public",
        effective_from=None,
        effective_to=None,
        metadata={
            "category": "product_care_sop",
            "approval_status": "pending_review",
        },
        internal_links=(),
    )
    await control.stage_version(
        draft=draft,
        chunks=(KnowledgeChunkRecord("care-review", "care-review-hash", 0, None),),
    )
    await control.mark_indexed(
        tenant_id="__global__",
        document_id="care-review-v1",
        version_id="care-review-v1",
        point_ids=("care-review-point",),
        index_namespace="test",
    )
    service = SiteKnowledgeReadinessService(
        catalog=InMemoryProductCatalog(),
        control_plane=control,
    )

    result = await service.get(GetSiteKnowledgeReadinessQuery(_principal(), "site-a"))

    assert not result.care_ready
    assert "published_care_knowledge_missing" in result.blocking_reasons
