import os
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from app.domain.ports import (
    KnowledgeChunkRecord,
    KnowledgeVersionDraft,
    KnowledgeVersionSchemaInvariantError,
)
from app.integrations.postgres import (
    DatabaseSessionManager,
    PostgreSQLKnowledgeControlPlaneAdapter,
)
from app.integrations.postgres.models import (
    AuditEventModel,
    KnowledgeChunkManifestModel,
    KnowledgeDocumentModel,
    KnowledgeDocumentVersionModel,
    KnowledgeSyncJobModel,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION_TESTS") != "1",
        reason="set RUN_INTEGRATION_TESTS=1 with PostgreSQL running",
    ),
]

DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/customer_agent",
)


async def test_chunk_manifest_id_can_be_reused_across_document_versions() -> None:
    tenant_id = f"test-{uuid4()}"
    document_id = str(uuid4())
    chunk_id = str(uuid4())
    manager = DatabaseSessionManager(DATABASE_URL)
    control_plane = PostgreSQLKnowledgeControlPlaneAdapter(manager.session_factory)
    chunk = KnowledgeChunkRecord(chunk_id, "chunk-hash", 0, "Shipping Notes")

    try:
        first = _draft(tenant_id, document_id, "version-1", "document-hash-1")
        second = _draft(tenant_id, document_id, "version-2", "document-hash-2")

        await control_plane.stage_version(draft=first, chunks=(chunk,))
        await control_plane.mark_indexed(
            tenant_id=tenant_id,
            document_id=document_id,
            version_id=first.version_id,
            point_ids=(chunk_id,),
            index_namespace="test-v1",
        )
        await control_plane.stage_version(draft=second, chunks=(chunk,))
        await control_plane.mark_indexed(
            tenant_id=tenant_id,
            document_id=document_id,
            version_id=second.version_id,
            point_ids=(chunk_id,),
            index_namespace="test-v1",
        )

        async with manager.session() as session:
            manifest_count = await session.scalar(
                select(func.count())
                .select_from(KnowledgeChunkManifestModel)
                .where(
                    KnowledgeChunkManifestModel.tenant_id == tenant_id,
                    KnowledgeChunkManifestModel.chunk_id == chunk_id,
                )
            )
        assert manifest_count == 2
        assert (
            await control_plane.get_latest_version(
                tenant_id=tenant_id,
                document_id=document_id,
            )
        ).version_id == second.version_id
    finally:
        async with manager.session_factory.begin() as session:
            for model in (
                KnowledgeChunkManifestModel,
                KnowledgeDocumentVersionModel,
                KnowledgeDocumentModel,
                AuditEventModel,
            ):
                await session.execute(delete(model).where(model.tenant_id == tenant_id))
        await manager.dispose()


async def test_active_web_document_validators_are_loaded_from_published_version() -> None:
    tenant_id = f"test-{uuid4()}"
    document_id = str(uuid4())
    version_id = str(uuid4())
    manager = DatabaseSessionManager(DATABASE_URL)
    control_plane = PostgreSQLKnowledgeControlPlaneAdapter(manager.session_factory)
    draft = _draft(
        tenant_id,
        document_id,
        version_id,
        "document-hash",
        metadata={
            "site_id": "site-a",
            "source_type": "website_html",
            "canonical_url": "https://shop.example.test/products/product.html",
            "requested_url": "https://shop.example.test/product.html?ref=agent",
            "final_url": "https://shop.example.test/products/product.html",
            "etag": '"product-v1"',
            "last_modified": "Sun, 27 Jul 2026 00:00:00 GMT",
        },
    )

    try:
        await control_plane.stage_version(draft=draft, chunks=())
        await control_plane.mark_indexed(
            tenant_id=tenant_id,
            document_id=document_id,
            version_id=version_id,
            point_ids=(),
            index_namespace="test-v1",
            activate=False,
        )
        await control_plane.activate_site_versions(
            tenant_id=tenant_id,
            site_id="site-a",
            version_ids=(version_id,),
        )

        active = await control_plane.list_active_site_web_documents(
            tenant_id=tenant_id,
            site_id="site-a",
        )

        assert len(active) == 1
        assert active[0].document_id == document_id
        assert active[0].version_id == version_id
        assert active[0].canonical_url.endswith("/products/product.html")
        assert active[0].requested_url.endswith("/product.html?ref=agent")
        assert active[0].etag == '"product-v1"'
        assert active[0].last_modified == "Sun, 27 Jul 2026 00:00:00 GMT"
    finally:
        async with manager.session_factory.begin() as session:
            for model in (
                KnowledgeChunkManifestModel,
                KnowledgeDocumentVersionModel,
                KnowledgeDocumentModel,
                AuditEventModel,
            ):
                await session.execute(delete(model).where(model.tenant_id == tenant_id))
        await manager.dispose()


async def test_active_approved_global_care_sop_marks_care_ready() -> None:
    tenant_id = "__global__"
    document_id = f"care-{uuid4()}"
    version_id = str(uuid4())
    manager = DatabaseSessionManager(DATABASE_URL)
    control_plane = PostgreSQLKnowledgeControlPlaneAdapter(manager.session_factory)
    draft = KnowledgeVersionDraft(
        version_id=version_id,
        tenant_id=tenant_id,
        document_id=document_id,
        source_path="care-sop.md",
        title="Approved care SOP",
        version="1.0.0",
        content_hash=str(uuid4()),
        status="published",
        authority_level=90,
        priority=90,
        language="en",
        audience="public",
        effective_from=None,
        effective_to=None,
        metadata={
            "category": "product_care_sop",
            "care_pack_id": "company-multi-material-care",
            "care_pack_version": "1.0.0",
            "care_scope": "company_global",
            "approval_status": "approved",
            "approval_references": ["supplier-care-approval"],
            "approved_steps": [
                {
                    "step_id": "care.general.keep-dry",
                    "instructions": {
                        "en": "Keep the product dry.",
                        "zh": "保持产品干燥。",
                    },
                }
            ],
        },
        internal_links=(),
    )

    try:
        await control_plane.stage_version(draft=draft, chunks=())
        await control_plane.mark_indexed(
            tenant_id=tenant_id,
            document_id=document_id,
            version_id=version_id,
            point_ids=(),
            index_namespace="test-v1",
        )

        assert await control_plane.has_active_approved_care_sop()
    finally:
        async with manager.session_factory.begin() as session:
            for model in (
                KnowledgeChunkManifestModel,
                KnowledgeDocumentVersionModel,
                KnowledgeDocumentModel,
            ):
                await session.execute(
                    delete(model).where(
                        model.tenant_id == tenant_id,
                        model.document_id == document_id,
                    )
                )
        await manager.dispose()


async def test_staged_sync_identity_and_indexed_chunk_count_are_idempotent() -> None:
    tenant_id = f"test-{uuid4()}"
    document_id = str(uuid4())
    version_id = str(uuid4())
    sync_job_id = str(uuid4())
    manager = DatabaseSessionManager(DATABASE_URL)
    control_plane = PostgreSQLKnowledgeControlPlaneAdapter(manager.session_factory)
    draft = _draft(
        tenant_id,
        document_id,
        version_id,
        "document-hash",
        metadata={"site_id": "site-a", "source_type": "website_html"},
    )
    chunks = (
        KnowledgeChunkRecord(str(uuid4()), "chunk-1", 0, "One"),
        KnowledgeChunkRecord(str(uuid4()), "chunk-2", 1, "Two"),
    )

    try:
        first = await control_plane.begin_sync(
            tenant_id=tenant_id,
            vault_path="https://shop.example.test",
            sync_job_id=sync_job_id,
        )
        second = await control_plane.begin_sync(
            tenant_id=tenant_id,
            vault_path="https://shop.example.test",
            sync_job_id=sync_job_id,
        )
        await control_plane.stage_version(draft=draft, chunks=chunks)
        await control_plane.mark_indexed(
            tenant_id=tenant_id,
            document_id=document_id,
            version_id=version_id,
            point_ids=tuple(item.chunk_id for item in chunks),
            index_namespace="test-v1",
            activate=False,
        )

        assert first == second == sync_job_id
        assert (
            await control_plane.count_indexed_chunks(
                tenant_id=tenant_id,
                version_ids=(version_id,),
            )
            == 2
        )
        assert (
            await control_plane.count_indexed_chunks(
                tenant_id=f"other-{tenant_id}",
                version_ids=(version_id,),
            )
            == 0
        )
    finally:
        async with manager.session_factory.begin() as session:
            for model in (
                KnowledgeChunkManifestModel,
                KnowledgeDocumentVersionModel,
                KnowledgeDocumentModel,
                KnowledgeSyncJobModel,
                AuditEventModel,
            ):
                await session.execute(delete(model).where(model.tenant_id == tenant_id))
        await manager.dispose()


async def test_discarded_staged_snapshot_versions_are_not_reusable() -> None:
    tenant_id = f"test-{uuid4()}"
    document_id = str(uuid4())
    version_id = str(uuid4())
    snapshot_id = str(uuid4())
    point_id = str(uuid4())
    manager = DatabaseSessionManager(DATABASE_URL)
    control_plane = PostgreSQLKnowledgeControlPlaneAdapter(manager.session_factory)
    draft = _draft(
        tenant_id,
        document_id,
        version_id,
        "discarded-document-hash",
        metadata={
            "site_id": "site-a",
            "snapshot_id": snapshot_id,
            "source_type": "website_html",
            "canonical_url": "https://shop.example.test/product.html",
        },
    )
    chunk = KnowledgeChunkRecord(point_id, "discarded-chunk-hash", 0, "Product")

    try:
        await control_plane.stage_version(draft=draft, chunks=(chunk,))
        await control_plane.mark_indexed(
            tenant_id=tenant_id,
            document_id=document_id,
            version_id=version_id,
            point_ids=(point_id,),
            index_namespace="test-v1",
            activate=False,
        )
        await control_plane.activate_site_versions(
            tenant_id=tenant_id,
            site_id="site-a",
            version_ids=(version_id,),
        )

        discarded_count = await control_plane.discard_site_snapshot_versions(
            tenant_id=tenant_id,
            site_id="site-a",
            snapshot_id=snapshot_id,
        )

        assert discarded_count == 1
        assert (
            await control_plane.count_indexed_chunks(
                tenant_id=tenant_id,
                version_ids=(version_id,),
            )
            == 0
        )
        async with manager.session() as session:
            version = await session.scalar(
                select(KnowledgeDocumentVersionModel).where(
                    KnowledgeDocumentVersionModel.tenant_id == tenant_id,
                    KnowledgeDocumentVersionModel.version_id == version_id,
                )
            )
            manifest = await session.scalar(
                select(KnowledgeChunkManifestModel).where(
                    KnowledgeChunkManifestModel.tenant_id == tenant_id,
                    KnowledgeChunkManifestModel.version_id == version_id,
                )
            )
            document = await session.scalar(
                select(KnowledgeDocumentModel).where(
                    KnowledgeDocumentModel.tenant_id == tenant_id,
                    KnowledgeDocumentModel.document_id == document_id,
                )
            )
        assert version is not None
        assert version.status == "discarded"
        assert version.index_status == "discarded"
        assert manifest is not None
        assert manifest.status == "discarded"
        assert manifest.qdrant_point_id is None
        assert document is not None
        assert document.current_version_id is None
        assert document.status == "superseded"
        assert not await control_plane.list_active_site_version_ids(
            tenant_id=tenant_id,
            site_id="site-a",
        )
    finally:
        async with manager.session_factory.begin() as session:
            for model in (
                KnowledgeChunkManifestModel,
                KnowledgeDocumentVersionModel,
                KnowledgeDocumentModel,
                AuditEventModel,
            ):
                await session.execute(delete(model).where(model.tenant_id == tenant_id))
        await manager.dispose()


async def test_discarded_content_can_be_materialized_in_a_new_snapshot() -> None:
    tenant_id = f"test-{uuid4()}"
    document_id = str(uuid4())
    version_id = str(uuid4())
    first_snapshot_id = str(uuid4())
    second_snapshot_id = str(uuid4())
    manager = DatabaseSessionManager(DATABASE_URL)
    control_plane = PostgreSQLKnowledgeControlPlaneAdapter(manager.session_factory)
    first = _draft(
        tenant_id,
        document_id,
        version_id,
        "stable-document-hash",
        metadata={
            "site_id": "site-a",
            "snapshot_id": first_snapshot_id,
            "source_type": "website_html",
        },
    )

    try:
        await control_plane.stage_version(draft=first, chunks=())
        await control_plane.mark_indexed(
            tenant_id=tenant_id,
            document_id=document_id,
            version_id=version_id,
            point_ids=(),
            index_namespace="test-v1",
            activate=False,
        )
        await control_plane.discard_site_snapshot_versions(
            tenant_id=tenant_id,
            site_id="site-a",
            snapshot_id=first_snapshot_id,
        )

        rebuilt_version_id = await control_plane.stage_version(
            draft=_draft(
                tenant_id,
                document_id,
                version_id,
                "stable-document-hash",
                metadata={
                    "site_id": "site-a",
                    "snapshot_id": second_snapshot_id,
                    "source_type": "website_html",
                },
            ),
            chunks=(),
        )

        assert rebuilt_version_id != version_id
        async with manager.session() as session:
            versions = tuple(
                (
                    await session.scalars(
                        select(KnowledgeDocumentVersionModel).where(
                            KnowledgeDocumentVersionModel.tenant_id == tenant_id,
                            KnowledgeDocumentVersionModel.document_id == document_id,
                        )
                    )
                ).all()
            )
        assert len(versions) == 2
        assert {version.content_hash for version in versions} == {"stable-document-hash"}
        assert {version.snapshot_id for version in versions} == {
            first_snapshot_id,
            second_snapshot_id,
        }
    finally:
        async with manager.session_factory.begin() as session:
            for model in (
                KnowledgeChunkManifestModel,
                KnowledgeDocumentVersionModel,
                KnowledgeDocumentModel,
                AuditEventModel,
            ):
                await session.execute(delete(model).where(model.tenant_id == tenant_id))
        await manager.dispose()


async def test_snapshot_allows_only_one_live_version_per_document() -> None:
    tenant_id = f"test-{uuid4()}"
    document_id = str(uuid4())
    snapshot_id = str(uuid4())
    manager = DatabaseSessionManager(DATABASE_URL)
    control_plane = PostgreSQLKnowledgeControlPlaneAdapter(manager.session_factory)

    try:
        await control_plane.stage_version(
            draft=_draft(
                tenant_id,
                document_id,
                str(uuid4()),
                "document-hash-1",
                metadata={"snapshot_id": snapshot_id},
            ),
            chunks=(),
        )
        with pytest.raises(KnowledgeVersionSchemaInvariantError):
            await control_plane.stage_version(
                draft=_draft(
                    tenant_id,
                    document_id,
                    str(uuid4()),
                    "document-hash-2",
                    metadata={"snapshot_id": snapshot_id},
                ),
                chunks=(),
            )
    finally:
        async with manager.session_factory.begin() as session:
            for model in (
                KnowledgeChunkManifestModel,
                KnowledgeDocumentVersionModel,
                KnowledgeDocumentModel,
                AuditEventModel,
            ):
                await session.execute(delete(model).where(model.tenant_id == tenant_id))
        await manager.dispose()


def _draft(
    tenant_id: str,
    document_id: str,
    version_id: str,
    content_hash: str,
    *,
    metadata: dict[str, object] | None = None,
) -> KnowledgeVersionDraft:
    return KnowledgeVersionDraft(
        version_id=version_id,
        tenant_id=tenant_id,
        document_id=document_id,
        source_path="https://shop.example.test/product.html",
        title="Product",
        version=version_id,
        content_hash=content_hash,
        status="published",
        authority_level=30,
        priority=50,
        language="en",
        audience="public",
        effective_from=None,
        effective_to=None,
        metadata=metadata or {},
        internal_links=(),
    )
