import os
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.domain.models import ProductDataStatus, ProductSnapshot
from app.integrations.postgres import DatabaseSessionManager, PostgreSQLProductCatalogAdapter
from app.integrations.postgres.models import (
    AuditEventModel,
    ProductCatalogSnapshotModel,
    ProductFactSnapshotModel,
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


async def test_staged_product_count_is_unique_and_tenant_scoped() -> None:
    tenant_id = f"test-{uuid4()}"
    site_id = "site-a"
    snapshot_id = str(uuid4())
    manager = DatabaseSessionManager(DATABASE_URL)
    catalog = PostgreSQLProductCatalogAdapter(manager.session_factory)
    product = ProductSnapshot(
        tenant_id=tenant_id,
        site_id=site_id,
        snapshot_id=snapshot_id,
        product_key="SKU-100",
        sku="SKU-100",
        mpn=None,
        name="Product",
        canonical_url="https://shop.example.test/product.html",
        brand=None,
        material=None,
        dimensions={},
        weight=None,
        price="599",
        currency="USD",
        stock_status="in_stock",
        shipping_warehouse=None,
        shipping_regions=(),
        fetched_at=datetime.now(UTC),
        content_hash="a" * 64,
        source_url="https://shop.example.test/product.html",
        etag=None,
        last_modified=None,
        status=ProductDataStatus.VALID,
        missing_count=0,
    )

    try:
        await catalog.begin_snapshot(
            tenant_id=tenant_id,
            site_id=site_id,
            snapshot_id=snapshot_id,
            sync_job_id=snapshot_id,
            started_at=datetime.now(UTC),
        )
        await catalog.stage_products(
            tenant_id=tenant_id,
            site_id=site_id,
            snapshot_id=snapshot_id,
            products=(product, product),
        )
        with pytest.raises(ValueError, match="product_identity_conflict:sku-100"):
            await catalog.stage_products(
                tenant_id=tenant_id,
                site_id=site_id,
                snapshot_id=snapshot_id,
                products=(
                    replace(
                        product,
                        product_key="sku-100",
                        sku="sku-100",
                        canonical_url="https://shop.example.test/other-product.html",
                        source_url="https://shop.example.test/other-product.html",
                        dimensions={"Height": "60 cm"},
                    ),
                ),
            )

        assert (
            await catalog.count_staged_products(
                tenant_id=tenant_id,
                site_id=site_id,
                snapshot_id=snapshot_id,
            )
            == 1
        )
        assert (
            await catalog.count_staged_products(
                tenant_id=f"other-{tenant_id}",
                site_id=site_id,
                snapshot_id=snapshot_id,
            )
            == 0
        )
    finally:
        async with manager.session_factory.begin() as session:
            for model in (
                ProductFactSnapshotModel,
                ProductCatalogSnapshotModel,
                AuditEventModel,
            ):
                await session.execute(delete(model).where(model.tenant_id == tenant_id))
        await manager.dispose()


async def test_product_snapshot_can_be_restored_after_publication_rollback() -> None:
    tenant_id = f"test-{uuid4()}"
    site_id = "site-a"
    previous_snapshot_id = str(uuid4())
    candidate_snapshot_id = str(uuid4())
    manager = DatabaseSessionManager(DATABASE_URL)
    catalog = PostgreSQLProductCatalogAdapter(manager.session_factory)
    product = ProductSnapshot(
        tenant_id=tenant_id,
        site_id=site_id,
        snapshot_id=previous_snapshot_id,
        product_key="SKU-RESTORE",
        sku="SKU-RESTORE",
        mpn=None,
        name="Restore Product",
        canonical_url="https://shop.example.test/restore.html",
        fetched_at=datetime.now(UTC),
        content_hash="b" * 64,
        source_url="https://shop.example.test/restore.html",
        status=ProductDataStatus.VALID,
    )

    try:
        for snapshot_id in (previous_snapshot_id, candidate_snapshot_id):
            await catalog.begin_snapshot(
                tenant_id=tenant_id,
                site_id=site_id,
                snapshot_id=snapshot_id,
                sync_job_id=snapshot_id,
                started_at=datetime.now(UTC),
            )
            await catalog.stage_products(
                tenant_id=tenant_id,
                site_id=site_id,
                snapshot_id=snapshot_id,
                products=(
                    replace(
                        product,
                        snapshot_id=snapshot_id,
                        price="599" if snapshot_id == previous_snapshot_id else "629",
                    ),
                ),
            )
            await catalog.activate_snapshot(
                tenant_id=tenant_id,
                site_id=site_id,
                snapshot_id=snapshot_id,
                completed_at=datetime.now(UTC),
            )

        await catalog.restore_snapshot(
            tenant_id=tenant_id,
            site_id=site_id,
            failed_snapshot_id=candidate_snapshot_id,
            previous_snapshot_id=previous_snapshot_id,
            restored_at=datetime.now(UTC),
            error_summary={"finalization": "Qdrant verification failed"},
        )

        summary = await catalog.get_active_summary(tenant_id=tenant_id, site_id=site_id)
        assert summary.snapshot_id == previous_snapshot_id
        async with manager.session() as session:
            snapshots = {
                item.snapshot_id: item
                for item in await session.scalars(
                    select(ProductCatalogSnapshotModel).where(
                        ProductCatalogSnapshotModel.tenant_id == tenant_id
                    )
                )
            }
        assert snapshots[previous_snapshot_id].status == "active"
        assert snapshots[candidate_snapshot_id].status == "staging"
        assert snapshots[candidate_snapshot_id].error_summary == {
            "finalization": "Qdrant verification failed"
        }
    finally:
        async with manager.session_factory.begin() as session:
            for model in (
                ProductFactSnapshotModel,
                ProductCatalogSnapshotModel,
                AuditEventModel,
            ):
                await session.execute(delete(model).where(model.tenant_id == tenant_id))
        await manager.dispose()
