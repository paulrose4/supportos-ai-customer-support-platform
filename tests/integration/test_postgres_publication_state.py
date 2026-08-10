import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.integrations.postgres import (
    DatabaseSessionManager,
    PostgreSQLKnowledgeControlPlaneAdapter,
)
from app.integrations.postgres.models import (
    KnowledgeDocumentModel,
    KnowledgeDocumentVersionModel,
    ProductCatalogSnapshotModel,
    SupportSiteModel,
    TenantModel,
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


async def test_site_publication_state_is_serialized_and_recoverable() -> None:
    tenant_id = f"test-{uuid4()}"
    site_id = "site-a"
    manager = DatabaseSessionManager(DATABASE_URL)
    control_plane = PostgreSQLKnowledgeControlPlaneAdapter(manager.session_factory)

    try:
        missing = await control_plane.get_site_publication_state(
            tenant_id=tenant_id,
            site_id=site_id,
        )
        assert missing.state == "missing"

        async with manager.session_factory.begin() as session:
            session.add_all(
                (
                    TenantModel(
                        tenant_id=tenant_id,
                        name="Publication Test Workspace",
                        status="active",
                        created_at=datetime.now(UTC),
                        updated_at=datetime.now(UTC),
                    ),
                    SupportSiteModel(
                        tenant_id=tenant_id,
                        site_id=site_id,
                        public_widget_id=f"widget-{uuid4().hex}",
                        name="Publication Test Site",
                        base_url="https://shop.example.test",
                        allowed_origins=["https://shop.example.test"],
                        status="active",
                    ),
                )
            )

        first_switch = await control_plane.begin_site_publication_switch(
            tenant_id=tenant_id,
            site_id=site_id,
            publication_id="publication-a",
        )
        assert first_switch.state == "active"
        assert first_switch.active_publication_id is None
        repeated_switch = await control_plane.begin_site_publication_switch(
            tenant_id=tenant_id,
            site_id=site_id,
            publication_id="publication-a",
        )
        assert repeated_switch.state == "active"
        assert repeated_switch.active_publication_id is None
        with pytest.raises(RuntimeError, match="already in progress"):
            await control_plane.begin_site_publication_switch(
                tenant_id=tenant_id,
                site_id=site_id,
                publication_id="publication-b",
            )

        await control_plane.complete_site_publication_switch(
            tenant_id=tenant_id,
            site_id=site_id,
            publication_id="publication-a",
        )
        second_switch = await control_plane.begin_site_publication_switch(
            tenant_id=tenant_id,
            site_id=site_id,
            publication_id="publication-b",
        )
        assert second_switch.state == "active"
        assert second_switch.active_publication_id == "publication-a"
        await control_plane.require_site_publication_recovery(
            tenant_id=tenant_id,
            site_id=site_id,
            publication_id="publication-b",
            error_code="rollback_failed:RuntimeError",
        )
        recovery = await control_plane.get_site_publication_state(
            tenant_id=tenant_id,
            site_id=site_id,
        )
        assert recovery.state == "recovery_required"
        assert recovery.active_publication_id == "publication-a"
        assert recovery.pending_publication_id == "publication-b"

        recovery_switch = await control_plane.begin_site_publication_switch(
            tenant_id=tenant_id,
            site_id=site_id,
            publication_id="publication-c",
        )
        assert recovery_switch.state == "recovery_required"
        assert recovery_switch.active_publication_id == "publication-a"
        await control_plane.require_site_publication_recovery(
            tenant_id=tenant_id,
            site_id=site_id,
            publication_id="publication-c",
            error_code="retry_failed:RuntimeError",
        )
        await control_plane.restore_site_publication(
            tenant_id=tenant_id,
            site_id=site_id,
            failed_publication_id="publication-c",
            previous_publication_id="publication-a",
            error_code="RuntimeError",
        )
        restored = await control_plane.get_site_publication_state(
            tenant_id=tenant_id,
            site_id=site_id,
        )
        assert restored.state == "active"
        assert restored.active_publication_id == "publication-a"
        assert restored.pending_publication_id is None
        assert restored.error_code == "RuntimeError"
    finally:
        async with manager.session_factory.begin() as session:
            await session.execute(
                delete(SupportSiteModel).where(SupportSiteModel.tenant_id == tenant_id)
            )
            await session.execute(delete(TenantModel).where(TenantModel.tenant_id == tenant_id))
        await manager.dispose()


async def test_site_publication_commit_rolls_back_all_postgres_projections() -> None:
    tenant_id = f"test-{uuid4()}"
    site_id = "site-a"
    publication_id = "publication-a"
    document_id = "document-a"
    version_id = "version-a"
    manager = DatabaseSessionManager(DATABASE_URL)
    control_plane = PostgreSQLKnowledgeControlPlaneAdapter(manager.session_factory)
    now = datetime.now(UTC)

    try:
        async with manager.session_factory.begin() as session:
            session.add_all(
                (
                    TenantModel(
                        tenant_id=tenant_id,
                        name="Atomic Publication Test Workspace",
                        status="active",
                        created_at=now,
                        updated_at=now,
                    ),
                    SupportSiteModel(
                        tenant_id=tenant_id,
                        site_id=site_id,
                        public_widget_id=f"widget-{uuid4().hex}",
                        name="Atomic Publication Test Site",
                        base_url="https://shop.example.test",
                        allowed_origins=["https://shop.example.test"],
                        status="active",
                    ),
                )
            )
            session.add(
                KnowledgeDocumentModel(
                    tenant_id=tenant_id,
                    document_id=document_id,
                    source_path="https://shop.example.test/product-a",
                    title="Product A",
                    status="staged",
                )
            )
            session.add(
                KnowledgeDocumentVersionModel(
                    tenant_id=tenant_id,
                    version_id=version_id,
                    document_id=document_id,
                    version="1",
                    content_hash="a" * 64,
                    status="published",
                    index_status="indexed",
                    authority_level=50,
                    priority=50,
                    language="en",
                    audience="public",
                    metadata_payload={
                        "site_id": site_id,
                        "source_type": "website_html",
                        "snapshot_id": publication_id,
                    },
                )
            )
            session.add(
                ProductCatalogSnapshotModel(
                    tenant_id=tenant_id,
                    site_id=site_id,
                    snapshot_id=publication_id,
                    sync_job_id=publication_id,
                    status="staging",
                    started_at=now,
                )
            )

        await control_plane.begin_site_publication_switch(
            tenant_id=tenant_id,
            site_id=site_id,
            publication_id=publication_id,
        )
        with pytest.raises(RuntimeError, match="knowledge sync job not found"):
            await control_plane.commit_site_publication(
                tenant_id=tenant_id,
                site_id=site_id,
                publication_id=publication_id,
                version_ids=(version_id,),
                discovered_count=1,
                indexed_count=1,
                error_summary={},
                completed_at=now,
            )

        async with manager.session_factory() as session:
            document = await session.scalar(
                select(KnowledgeDocumentModel).where(
                    KnowledgeDocumentModel.tenant_id == tenant_id,
                    KnowledgeDocumentModel.document_id == document_id,
                )
            )
            version = await session.scalar(
                select(KnowledgeDocumentVersionModel).where(
                    KnowledgeDocumentVersionModel.tenant_id == tenant_id,
                    KnowledgeDocumentVersionModel.version_id == version_id,
                )
            )
            product_snapshot = await session.scalar(
                select(ProductCatalogSnapshotModel).where(
                    ProductCatalogSnapshotModel.tenant_id == tenant_id,
                    ProductCatalogSnapshotModel.snapshot_id == publication_id,
                )
            )

        assert document is not None and document.current_version_id is None
        assert version is not None and version.index_status == "indexed"
        assert product_snapshot is not None and product_snapshot.status == "staging"
        publication = await control_plane.get_site_publication_state(
            tenant_id=tenant_id,
            site_id=site_id,
        )
        assert publication.state == "switching"
        assert publication.pending_publication_id == publication_id
    finally:
        async with manager.session_factory.begin() as session:
            for model in (
                ProductCatalogSnapshotModel,
                KnowledgeDocumentVersionModel,
                KnowledgeDocumentModel,
                SupportSiteModel,
                TenantModel,
            ):
                await session.execute(delete(model).where(model.tenant_id == tenant_id))
        await manager.dispose()
