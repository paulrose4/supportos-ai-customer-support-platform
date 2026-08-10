import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, update

from app.application.tenant_context import tenant_scope
from app.domain.models import (
    WebSyncJob,
    WebSyncJobItem,
    WebSyncJobItemStatus,
    WebSyncJobPhase,
    WebSyncJobReport,
    WebSyncJobStatus,
    WebSyncMode,
    WebSyncPolicySnapshot,
    WebSyncPublicationStatus,
    WebSyncTrigger,
)
from app.domain.ports.web_sync_jobs import WebSyncSourceConfigVersionConflictError
from app.integrations.postgres import DatabaseSessionManager, PostgreSQLWebSyncJobStore
from app.integrations.postgres.models import (
    AuditEventModel,
    SiteWebSourceConfigModel,
    SupportSiteModel,
    TenantModel,
    WebSyncJobItemModel,
    WebSyncJobModel,
    WebSyncProductIdentityDecisionModel,
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


async def test_web_sync_job_lease_and_site_deduplication() -> None:
    tenant_id = f"test-{uuid4()}"
    site_id = f"site-{uuid4()}"
    now = datetime.now(UTC)
    manager = DatabaseSessionManager(DATABASE_URL)
    store = PostgreSQLWebSyncJobStore(manager.session_factory)
    try:
        with tenant_scope(tenant_id):
            async with manager.session_factory.begin() as session:
                session.add_all(
                    (
                        _tenant(tenant_id, now),
                        SupportSiteModel(
                            tenant_id=tenant_id,
                            site_id=site_id,
                            public_widget_id=f"site_pub_{uuid4().hex}",
                            name="Web sync integration",
                            base_url="https://shop.example.com",
                            allowed_origins=["https://shop.example.com"],
                            widget_daily_message_limit=100,
                            primary_language="en",
                            status="active",
                            created_at=now,
                            updated_at=now,
                        ),
                    )
                )
            first, created = await store.enqueue(_job(tenant_id, site_id, "request-1", now))
            duplicate, duplicate_created = await store.enqueue(
                _job(tenant_id, site_id, "request-2", now)
            )

            assert created is True
            assert duplicate_created is False
            assert duplicate.job_id == first.job_id

            claimed = await store.claim_next(
                tenant_id=tenant_id,
                worker_id="worker-1",
                claimed_at=now,
                lease_expires_at=now + timedelta(minutes=2),
            )
            assert claimed is not None
            assert claimed.status == WebSyncJobStatus.RUNNING
            assert claimed.attempt_count == 1

            completed = await store.complete(
                tenant_id=tenant_id,
                job_id=claimed.job_id,
                worker_id="worker-1",
                report=_report(),
                completed_at=now + timedelta(minutes=1),
            )
            assert completed.status == WebSyncJobStatus.SUCCEEDED
            assert completed.phase == WebSyncJobPhase.COMPLETED
            assert completed.report == _report()

            next_job, next_created = await store.enqueue(
                _job(tenant_id, site_id, "request-3", now + timedelta(minutes=2))
            )
            assert next_created is True
            assert next_job.job_id != first.job_id
    finally:
        with tenant_scope(tenant_id):
            async with manager.session_factory.begin() as session:
                await session.execute(
                    delete(AuditEventModel).where(AuditEventModel.tenant_id == tenant_id)
                )
                await session.execute(
                    delete(WebSyncJobModel).where(WebSyncJobModel.tenant_id == tenant_id)
                )
                await session.execute(
                    delete(SupportSiteModel).where(SupportSiteModel.tenant_id == tenant_id)
                )
                await session.execute(delete(TenantModel).where(TenantModel.tenant_id == tenant_id))
        await manager.dispose()


async def test_enqueue_rejects_a_manifest_from_an_outdated_source_config() -> None:
    tenant_id = f"test-{uuid4()}"
    site_id = f"site-{uuid4()}"
    now = datetime.now(UTC)
    manager = DatabaseSessionManager(DATABASE_URL)
    store = PostgreSQLWebSyncJobStore(manager.session_factory)
    try:
        with tenant_scope(tenant_id):
            async with manager.session_factory.begin() as session:
                session.add_all(
                    (
                        _tenant(tenant_id, now),
                        SupportSiteModel(
                            tenant_id=tenant_id,
                            site_id=site_id,
                            public_widget_id=f"site_pub_{uuid4().hex}",
                            name="Web sync source version integration",
                            base_url="https://shop.example.com",
                            allowed_origins=["https://shop.example.com"],
                            widget_daily_message_limit=100,
                            primary_language="en",
                            status="active",
                            created_at=now,
                            updated_at=now,
                        ),
                    )
                )
                session.add(
                    SiteWebSourceConfigModel(
                        tenant_id=tenant_id,
                        site_id=site_id,
                        discovery_mode="hybrid",
                        explicit_sitemap_urls=["https://shop.example.com/new-map.xml"],
                        config_version=2,
                        validation_status="unvalidated",
                        validated_at=None,
                        updated_by="admin-a",
                        updated_at=now,
                    )
                )

            with pytest.raises(WebSyncSourceConfigVersionConflictError):
                await store.enqueue(
                    _job(tenant_id, site_id, "stale-source-config", now),
                    expected_source_config_version=1,
                )

            async with manager.session_factory() as session:
                persisted = await session.scalar(
                    select(WebSyncJobModel).where(
                        WebSyncJobModel.tenant_id == tenant_id,
                        WebSyncJobModel.site_id == site_id,
                    )
                )
            assert persisted is None
    finally:
        with tenant_scope(tenant_id):
            async with manager.session_factory.begin() as session:
                await session.execute(
                    delete(AuditEventModel).where(AuditEventModel.tenant_id == tenant_id)
                )
                await session.execute(
                    delete(WebSyncJobModel).where(WebSyncJobModel.tenant_id == tenant_id)
                )
                await session.execute(
                    delete(SiteWebSourceConfigModel).where(
                        SiteWebSourceConfigModel.tenant_id == tenant_id
                    )
                )
                await session.execute(
                    delete(SupportSiteModel).where(SupportSiteModel.tenant_id == tenant_id)
                )
                await session.execute(delete(TenantModel).where(TenantModel.tenant_id == tenant_id))
        await manager.dispose()


async def test_same_site_identity_is_independent_across_tenants() -> None:
    suffix = uuid4().hex
    tenant_ids = (f"tenant-a-{suffix}", f"tenant-b-{suffix}")
    site_id = "shared-site-id"
    now = datetime.now(UTC)
    manager = DatabaseSessionManager(DATABASE_URL)
    store = PostgreSQLWebSyncJobStore(manager.session_factory)
    try:
        async with manager.session_factory.begin() as session:
            session.add_all(_tenant(tenant_id, now) for tenant_id in tenant_ids)
        for tenant_id in tenant_ids:
            with tenant_scope(tenant_id):
                async with manager.session_factory.begin() as session:
                    session.add(
                        SupportSiteModel(
                            tenant_id=tenant_id,
                            site_id=site_id,
                            public_widget_id=f"site_pub_{uuid4().hex}",
                            name=f"Site for {tenant_id}",
                            base_url="https://shop.example.com",
                            allowed_origins=["https://shop.example.com"],
                            widget_daily_message_limit=100,
                            primary_language="en",
                            status="active",
                            created_at=now,
                            updated_at=now,
                        )
                    )
                queued, created = await store.enqueue(
                    _job(tenant_id, site_id, "same-idempotency-key", now)
                )
                assert created is True
                assert queued.tenant_id == tenant_id

        for tenant_id in tenant_ids:
            with tenant_scope(tenant_id):
                visible = await store.list_recent(
                    tenant_id=tenant_id,
                    site_id=site_id,
                    limit=10,
                )
                assert [(item.tenant_id, item.site_id) for item in visible] == [
                    (tenant_id, site_id)
                ]
                claimed = await store.claim_next(
                    tenant_id=tenant_id,
                    worker_id=f"worker-{tenant_id}",
                    claimed_at=now,
                    lease_expires_at=now + timedelta(minutes=2),
                )
                assert claimed is not None
                assert claimed.tenant_id == tenant_id
                assert claimed.site_id == site_id
    finally:
        for tenant_id in tenant_ids:
            with tenant_scope(tenant_id):
                async with manager.session_factory.begin() as session:
                    await session.execute(
                        delete(AuditEventModel).where(AuditEventModel.tenant_id == tenant_id)
                    )
                    await session.execute(
                        delete(WebSyncJobModel).where(WebSyncJobModel.tenant_id == tenant_id)
                    )
                    await session.execute(
                        delete(SupportSiteModel).where(SupportSiteModel.tenant_id == tenant_id)
                    )
        async with manager.session_factory.begin() as session:
            await session.execute(delete(TenantModel).where(TenantModel.tenant_id.in_(tenant_ids)))
        await manager.dispose()


@pytest.mark.parametrize(
    ("mode", "expected_publication_status"),
    (
        (WebSyncMode.SHADOW, WebSyncPublicationStatus.NOT_REQUESTED),
        (WebSyncMode.PRODUCTION, WebSyncPublicationStatus.REFUSED),
    ),
)
async def test_page_checkpoints_survive_job_lease_recovery_and_can_be_canceled(
    mode: WebSyncMode,
    expected_publication_status: WebSyncPublicationStatus,
) -> None:
    tenant_id = f"test-{uuid4()}"
    site_id = f"site-{uuid4()}"
    now = datetime.now(UTC)
    manager = DatabaseSessionManager(DATABASE_URL)
    store = PostgreSQLWebSyncJobStore(manager.session_factory)
    job = _job(tenant_id, site_id, "resumable-request", now)
    job = replace(
        job,
        mode=mode,
        publication_status=(
            WebSyncPublicationStatus.NOT_REQUESTED
            if mode is WebSyncMode.SHADOW
            else WebSyncPublicationStatus.PENDING
        ),
        expected_count=2,
    )
    items = tuple(
        WebSyncJobItem(
            tenant_id=tenant_id,
            job_id=job.job_id,
            site_id=site_id,
            manifest_id="manifest-a",
            item_id=f"item-{index}",
            ordinal=index,
            url=f"https://shop.example.com/product-{index}.html",
            source_sitemap_url="https://shop.example.com/sitemap-products.xml",
        )
        for index in range(2)
    )
    try:
        with tenant_scope(tenant_id):
            async with manager.session_factory.begin() as session:
                session.add_all(
                    (
                        _tenant(tenant_id, now),
                        SupportSiteModel(
                            tenant_id=tenant_id,
                            site_id=site_id,
                            public_widget_id=f"site_pub_{uuid4().hex}",
                            name="Resumable web sync",
                            base_url="https://shop.example.com",
                            allowed_origins=["https://shop.example.com"],
                            widget_daily_message_limit=100,
                            primary_language="en",
                            status="active",
                            created_at=now,
                            updated_at=now,
                        ),
                    )
                )
            _, created = await store.enqueue(job, items)
            assert created is True
            claimed = await store.claim_next(
                tenant_id=tenant_id,
                worker_id="worker-1",
                claimed_at=now,
                lease_expires_at=now + timedelta(seconds=10),
            )
            assert claimed is not None
            first = await store.claim_next_item(
                tenant_id=tenant_id,
                job_id=job.job_id,
                worker_id="worker-1",
                claimed_at=now,
                lease_expires_at=now + timedelta(seconds=10),
            )
            assert first is not None
            await store.complete_item(
                tenant_id=tenant_id,
                job_id=job.job_id,
                item_id=first.item_id,
                worker_id="worker-1",
                status=WebSyncJobItemStatus.NOT_MODIFIED,
                report=replace(_report(), published=False),
                validator=replace(
                    first,
                    document_id="document-1",
                    version_id="version-1",
                    canonical_url=first.url,
                    final_url=first.url,
                    etag='"etag-1"',
                    product_key="sku-1",
                ),
                duration_ms=25,
                completed_at=now + timedelta(seconds=1),
            )

            recovered = await store.claim_next(
                tenant_id=tenant_id,
                worker_id="worker-2",
                claimed_at=now + timedelta(seconds=11),
                lease_expires_at=now + timedelta(minutes=2),
            )
            assert recovered is not None
            second = await store.claim_next_item(
                tenant_id=tenant_id,
                job_id=job.job_id,
                worker_id="worker-2",
                claimed_at=now + timedelta(seconds=11),
                lease_expires_at=now + timedelta(minutes=2),
            )
            assert second is not None
            assert second.item_id == "item-1"

            requested = await store.request_cancel(
                tenant_id=tenant_id,
                job_id=job.job_id,
                requested_by="admin-1",
                requested_at=now + timedelta(seconds=12),
            )
            assert requested.cancel_requested_at is not None
            canceled = await store.cancel(
                tenant_id=tenant_id,
                job_id=job.job_id,
                worker_id="worker-2",
                canceled_at=now + timedelta(seconds=13),
            )
            assert canceled.status is WebSyncJobStatus.CANCELED
            assert canceled.completed_count == 2
            assert canceled.not_modified_count == 1
            assert canceled.canceled_item_count == 1
            assert canceled.publication_status is expected_publication_status
    finally:
        with tenant_scope(tenant_id):
            async with manager.session_factory.begin() as session:
                await session.execute(
                    delete(AuditEventModel).where(AuditEventModel.tenant_id == tenant_id)
                )
                await session.execute(
                    delete(WebSyncJobModel).where(WebSyncJobModel.tenant_id == tenant_id)
                )
                await session.execute(
                    delete(SupportSiteModel).where(SupportSiteModel.tenant_id == tenant_id)
                )
                await session.execute(delete(TenantModel).where(TenantModel.tenant_id == tenant_id))
        await manager.dispose()


async def test_stale_worker_cannot_mutate_page_after_job_lease_recovery() -> None:
    tenant_id = f"test-{uuid4()}"
    site_id = f"site-{uuid4()}"
    now = datetime.now(UTC)
    manager = DatabaseSessionManager(DATABASE_URL)
    store = PostgreSQLWebSyncJobStore(manager.session_factory)
    job = replace(
        _job(tenant_id, site_id, "stale-page-owner", now),
        expected_count=1,
    )
    item = WebSyncJobItem(
        tenant_id=tenant_id,
        job_id=job.job_id,
        site_id=site_id,
        manifest_id=job.manifest_id,
        item_id="item-0",
        ordinal=0,
        url="https://shop.example.com/product-0.html",
        source_sitemap_url="https://shop.example.com/sitemap-products.xml",
    )
    try:
        with tenant_scope(tenant_id):
            async with manager.session_factory.begin() as session:
                session.add_all(
                    (
                        _tenant(tenant_id, now),
                        SupportSiteModel(
                            tenant_id=tenant_id,
                            site_id=site_id,
                            public_widget_id=f"site_pub_{uuid4().hex}",
                            name="Stale page owner fencing",
                            base_url="https://shop.example.com",
                            allowed_origins=["https://shop.example.com"],
                            widget_daily_message_limit=100,
                            primary_language="en",
                            status="active",
                            created_at=now,
                            updated_at=now,
                        ),
                    )
                )
            _, created = await store.enqueue(job, (item,))
            assert created is True

            claimed_by_a = await store.claim_next(
                tenant_id=tenant_id,
                worker_id="worker-a",
                claimed_at=now,
                lease_expires_at=now + timedelta(seconds=10),
            )
            assert claimed_by_a is not None
            page_claimed_by_a = await store.claim_next_item(
                tenant_id=tenant_id,
                job_id=job.job_id,
                worker_id="worker-a",
                claimed_at=now,
                lease_expires_at=now + timedelta(seconds=10),
            )
            assert page_claimed_by_a is not None

            claimed_by_b = await store.claim_next(
                tenant_id=tenant_id,
                worker_id="worker-b",
                claimed_at=now + timedelta(seconds=11),
                lease_expires_at=now + timedelta(minutes=2),
            )
            assert claimed_by_b is not None
            assert claimed_by_b.lease_owner == "worker-b"

            job_before_stale_write = await store.get(tenant_id=tenant_id, job_id=job.job_id)
            pages_before_stale_write = await store.list_items(
                tenant_id=tenant_id,
                job_id=job.job_id,
                status=None,
                limit=10,
            )
            assert job_before_stale_write is not None
            assert len(pages_before_stale_write) == 1
            page_before_stale_write = pages_before_stale_write[0]
            assert page_before_stale_write.status is WebSyncJobItemStatus.FETCHING
            assert page_before_stale_write.lease_owner == "worker-a"
            job_snapshot = (
                job_before_stale_write.completed_count,
                job_before_stale_write.succeeded_count,
                job_before_stale_write.not_modified_count,
                job_before_stale_write.excluded_item_count,
                job_before_stale_write.failed_item_count,
                job_before_stale_write.state_version,
            )
            page_snapshot = (
                page_before_stale_write.status,
                page_before_stale_write.attempt_count,
                page_before_stale_write.lease_owner,
                page_before_stale_write.lease_expires_at,
                page_before_stale_write.completed_at,
                page_before_stale_write.error_code,
            )

            with pytest.raises(RuntimeError, match="job lease is no longer owned"):
                await store.complete_item(
                    tenant_id=tenant_id,
                    job_id=job.job_id,
                    item_id=item.item_id,
                    worker_id="worker-a",
                    status=WebSyncJobItemStatus.SUCCEEDED,
                    report=replace(_report(), published=False),
                    validator=None,
                    duration_ms=25,
                    completed_at=now + timedelta(seconds=12),
                )
            await _assert_job_and_page_snapshot(
                store,
                tenant_id=tenant_id,
                job_id=job.job_id,
                job_snapshot=job_snapshot,
                page_snapshot=page_snapshot,
            )

            with pytest.raises(RuntimeError, match="job lease is no longer owned"):
                await store.fail_item(
                    tenant_id=tenant_id,
                    job_id=job.job_id,
                    item_id=item.item_id,
                    worker_id="worker-a",
                    error_code="stale_worker_failure",
                    error_message="stale worker must not update the page",
                    duration_ms=30,
                    failed_at=now + timedelta(seconds=13),
                )
            await _assert_job_and_page_snapshot(
                store,
                tenant_id=tenant_id,
                job_id=job.job_id,
                job_snapshot=job_snapshot,
                page_snapshot=page_snapshot,
            )

            page_claimed_by_b = await store.claim_next_item(
                tenant_id=tenant_id,
                job_id=job.job_id,
                worker_id="worker-b",
                claimed_at=now + timedelta(seconds=14),
                lease_expires_at=now + timedelta(minutes=2),
            )
            assert page_claimed_by_b is not None
            assert page_claimed_by_b.item_id == item.item_id
            assert page_claimed_by_b.lease_owner == "worker-b"
            assert page_claimed_by_b.attempt_count == 2

            completed_page = await store.complete_item(
                tenant_id=tenant_id,
                job_id=job.job_id,
                item_id=item.item_id,
                worker_id="worker-b",
                status=WebSyncJobItemStatus.SUCCEEDED,
                report=replace(_report(), published=False),
                validator=replace(
                    page_claimed_by_b,
                    document_id="document-0",
                    version_id="version-0",
                    canonical_url=page_claimed_by_b.url,
                    final_url=page_claimed_by_b.url,
                ),
                duration_ms=35,
                completed_at=now + timedelta(seconds=15),
            )
            assert completed_page.status is WebSyncJobItemStatus.SUCCEEDED
            assert completed_page.lease_owner is None

            completed_job = await store.get(tenant_id=tenant_id, job_id=job.job_id)
            assert completed_job is not None
            assert completed_job.completed_count == 1
            assert completed_job.succeeded_count == 1
            assert completed_job.failed_item_count == 0
    finally:
        with tenant_scope(tenant_id):
            async with manager.session_factory.begin() as session:
                await session.execute(
                    delete(AuditEventModel).where(AuditEventModel.tenant_id == tenant_id)
                )
                await session.execute(
                    delete(WebSyncJobModel).where(WebSyncJobModel.tenant_id == tenant_id)
                )
                await session.execute(
                    delete(SupportSiteModel).where(SupportSiteModel.tenant_id == tenant_id)
                )
                await session.execute(delete(TenantModel).where(TenantModel.tenant_id == tenant_id))
        await manager.dispose()


async def test_duplicate_policy_batch_resumes_without_locking_the_full_job() -> None:
    tenant_id = f"test-{uuid4()}"
    site_id = f"site-{uuid4()}"
    now = datetime.now(UTC)
    manager = DatabaseSessionManager(DATABASE_URL)
    store = PostgreSQLWebSyncJobStore(manager.session_factory)
    job = replace(
        _job(tenant_id, site_id, "batched-duplicates", now),
        status=WebSyncJobStatus.PREPARING,
        phase=WebSyncJobPhase.PREPARING,
        expected_count=258,
        prepared_count=258,
    )
    product_keys = tuple(
        "SKU-A" if index == 0 else "sku-a" if index == 257 else f"sku-{index}"
        for index in range(258)
    )
    items = tuple(
        WebSyncJobItem(
            tenant_id=tenant_id,
            job_id=job.job_id,
            site_id=site_id,
            manifest_id=job.manifest_id,
            item_id=f"item-{index}",
            ordinal=index,
            url=f"https://shop.example.com/product-{index}.html",
            source_sitemap_url="https://shop.example.com/sitemap-products.xml",
            product_key=product_key,
        )
        for index, product_key in enumerate(product_keys)
    )
    try:
        with tenant_scope(tenant_id):
            async with manager.session_factory.begin() as session:
                session.add_all(
                    (
                        _tenant(tenant_id, now),
                        SupportSiteModel(
                            tenant_id=tenant_id,
                            site_id=site_id,
                            public_widget_id=f"site_pub_{uuid4().hex}",
                            name="Batched duplicate policy",
                            base_url="https://shop.example.com",
                            allowed_origins=["https://shop.example.com"],
                            widget_daily_message_limit=100,
                            primary_language="en",
                            status="active",
                            created_at=now,
                            updated_at=now,
                        ),
                    )
                )
            _, created = await store.enqueue(job, items)
            assert created is True

            claimed = await store.claim_next(
                tenant_id=tenant_id,
                worker_id="worker-duplicates",
                claimed_at=now,
                lease_expires_at=now + timedelta(minutes=2),
            )
            assert claimed is not None
            cursor = 0
            complete = False
            while not complete:
                _excluded, cursor, complete = await store.apply_duplicate_policy_batch(
                    tenant_id=tenant_id,
                    job_id=job.job_id,
                    worker_id="worker-duplicates",
                    policy=job.policy,
                    cursor=cursor,
                    limit=20,
                )
                if not complete:
                    await store.yield_preparation(
                        tenant_id=tenant_id,
                        job_id=job.job_id,
                        worker_id="worker-duplicates",
                        stage="apply_duplicate_policy",
                        cursor=cursor,
                        yielded_at=now,
                    )
                    claimed = await store.claim_next(
                        tenant_id=tenant_id,
                        worker_id="worker-duplicates",
                        claimed_at=now,
                        lease_expires_at=now + timedelta(minutes=2),
                    )
                    assert claimed is not None

            persisted = await store.list_items(
                tenant_id=tenant_id,
                job_id=job.job_id,
                status=None,
                limit=300,
            )
            assert len(persisted) == 258
            assert persisted[0].status is WebSyncJobItemStatus.PENDING
            assert persisted[257].status is WebSyncJobItemStatus.EXCLUDED
            assert persisted[257].winner_item_id == persisted[0].item_id
            assert {item.winner_item_id for item in (persisted[0], persisted[257])} == {
                persisted[0].item_id
            }
            current = await store.get(tenant_id=tenant_id, job_id=job.job_id)
            assert current is not None
            assert current.completed_count == 1
            assert current.excluded_item_count == 1

            async with manager.session_factory.begin() as session:
                await session.execute(
                    update(WebSyncJobItemModel)
                    .where(
                        WebSyncJobItemModel.tenant_id == tenant_id,
                        WebSyncJobItemModel.job_id == job.job_id,
                        WebSyncJobItemModel.item_id == "item-0",
                    )
                    .values(status=WebSyncJobItemStatus.SUCCEEDED.value)
                )
                await session.execute(
                    update(WebSyncJobModel)
                    .where(
                        WebSyncJobModel.tenant_id == tenant_id,
                        WebSyncJobModel.job_id == job.job_id,
                    )
                    .values(
                        status=WebSyncJobStatus.SUCCEEDED.value,
                        publication_status=WebSyncPublicationStatus.PUBLISHED.value,
                        completed_at=now,
                        active_site_key=None,
                        lease_owner=None,
                        lease_expires_at=None,
                    )
                )

            next_job = replace(
                _job(tenant_id, site_id, "reuse-published-winner", now + timedelta(seconds=1)),
                status=WebSyncJobStatus.PREPARING,
                phase=WebSyncJobPhase.PREPARING,
                expected_count=2,
                prepared_count=2,
            )
            next_items = (
                WebSyncJobItem(
                    tenant_id=tenant_id,
                    job_id=next_job.job_id,
                    site_id=site_id,
                    manifest_id=next_job.manifest_id,
                    item_id="next-item-0",
                    ordinal=0,
                    url="https://shop.example.com/new-candidate.html",
                    source_sitemap_url="https://shop.example.com/sitemap-products.xml",
                    product_key="sku-a",
                ),
                WebSyncJobItem(
                    tenant_id=tenant_id,
                    job_id=next_job.job_id,
                    site_id=site_id,
                    manifest_id=next_job.manifest_id,
                    item_id="next-item-1",
                    ordinal=1,
                    url="https://shop.example.com/product-0.html",
                    source_sitemap_url="https://shop.example.com/sitemap-products.xml",
                    product_key="SKU-A",
                ),
            )
            _, created = await store.enqueue(next_job, next_items)
            assert created is True
            claimed = await store.claim_next(
                tenant_id=tenant_id,
                worker_id="worker-reuse",
                claimed_at=now + timedelta(seconds=1),
                lease_expires_at=now + timedelta(minutes=2),
            )
            assert claimed is not None
            assert (
                await store.apply_duplicate_policy(
                    tenant_id=tenant_id,
                    job_id=next_job.job_id,
                    worker_id="worker-reuse",
                    policy=next_job.policy,
                )
                == 1
            )
            next_persisted = await store.list_items(
                tenant_id=tenant_id,
                job_id=next_job.job_id,
                status=None,
                limit=10,
            )
            assert next_persisted[0].status is WebSyncJobItemStatus.EXCLUDED
            assert next_persisted[1].status is WebSyncJobItemStatus.PENDING
            assert {item.winner_item_id for item in next_persisted} == {"next-item-1"}
    finally:
        with tenant_scope(tenant_id):
            async with manager.session_factory.begin() as session:
                await session.execute(
                    delete(AuditEventModel).where(AuditEventModel.tenant_id == tenant_id)
                )
                await session.execute(
                    delete(WebSyncJobModel).where(WebSyncJobModel.tenant_id == tenant_id)
                )
                await session.execute(
                    delete(SupportSiteModel).where(SupportSiteModel.tenant_id == tenant_id)
                )
                await session.execute(delete(TenantModel).where(TenantModel.tenant_id == tenant_id))
        await manager.dispose()


async def test_duplicate_fallback_promotion_is_atomic_and_idempotent() -> None:
    tenant_id = f"test-{uuid4()}"
    site_id = f"site-{uuid4()}"
    now = datetime.now(UTC)
    manager = DatabaseSessionManager(DATABASE_URL)
    store = PostgreSQLWebSyncJobStore(manager.session_factory)
    job = replace(
        _job(tenant_id, site_id, "fallback-promotion", now),
        status=WebSyncJobStatus.PREPARING,
        phase=WebSyncJobPhase.PREPARING,
        expected_count=3,
        prepared_count=3,
        manifest_fingerprint="manifest-fingerprint",
    )
    items = tuple(
        WebSyncJobItem(
            tenant_id=tenant_id,
            job_id=job.job_id,
            site_id=site_id,
            manifest_id=job.manifest_id,
            item_id=f"item-{index}",
            ordinal=index,
            url=f"https://shop.example.com/fallback-{index}.html",
            source_sitemap_url="https://shop.example.com/sitemap-products.xml",
            product_key=product_key,
        )
        for index, product_key in enumerate(("C08-B1023", "c08-b1023", "Ｃ０８－Ｂ１０２３"))
    )
    try:
        with tenant_scope(tenant_id):
            async with manager.session_factory.begin() as session:
                session.add_all(
                    (
                        _tenant(tenant_id, now),
                        SupportSiteModel(
                            tenant_id=tenant_id,
                            site_id=site_id,
                            public_widget_id=f"site_pub_{uuid4().hex}",
                            name="Fallback promotion",
                            base_url="https://shop.example.com",
                            allowed_origins=["https://shop.example.com"],
                            widget_daily_message_limit=100,
                            primary_language="en",
                            status="active",
                            created_at=now,
                            updated_at=now,
                        ),
                    )
                )
            await store.enqueue(job, items)
            claimed = await store.claim_next(
                tenant_id=tenant_id,
                worker_id="worker-fallback",
                claimed_at=now,
                lease_expires_at=now + timedelta(minutes=2),
            )
            assert claimed is not None
            assert (
                await store.apply_duplicate_policy(
                    tenant_id=tenant_id,
                    job_id=job.job_id,
                    worker_id="worker-fallback",
                    policy=job.policy,
                )
                == 2
            )
            await store.finish_preparation(
                tenant_id=tenant_id,
                job_id=job.job_id,
                worker_id="worker-fallback",
                manifest_fingerprint="manifest-fingerprint",
            )
            running = await store.claim_next(
                tenant_id=tenant_id,
                worker_id="worker-fallback",
                claimed_at=now + timedelta(seconds=1),
                lease_expires_at=now + timedelta(minutes=2),
            )
            assert running is not None and running.status is WebSyncJobStatus.RUNNING
            async with manager.session_factory.begin() as session:
                await session.execute(
                    update(WebSyncJobItemModel)
                    .where(
                        WebSyncJobItemModel.tenant_id == tenant_id,
                        WebSyncJobItemModel.job_id == job.job_id,
                        WebSyncJobItemModel.item_id == "item-0",
                    )
                    .values(
                        status=WebSyncJobItemStatus.FAILED.value,
                        outcome_reason="terminal_fetch_failure",
                        error_code="RuntimeError",
                        completed_at=now + timedelta(seconds=2),
                    )
                )
                await session.execute(
                    update(WebSyncJobModel)
                    .where(
                        WebSyncJobModel.tenant_id == tenant_id,
                        WebSyncJobModel.job_id == job.job_id,
                    )
                    .values(completed_count=3, failed_item_count=1)
                )

            assert (
                await store.promote_duplicate_fallbacks(
                    tenant_id=tenant_id,
                    job_id=job.job_id,
                    worker_id="worker-fallback",
                )
                == 1
            )
            assert (
                await store.promote_duplicate_fallbacks(
                    tenant_id=tenant_id,
                    job_id=job.job_id,
                    worker_id="worker-fallback",
                )
                == 0
            )
            persisted = await store.list_items(
                tenant_id=tenant_id,
                job_id=job.job_id,
                status=None,
                limit=10,
            )
            assert [item.status for item in persisted] == [
                WebSyncJobItemStatus.FAILED,
                WebSyncJobItemStatus.PENDING,
                WebSyncJobItemStatus.EXCLUDED,
            ]
            assert {item.winner_item_id for item in persisted} == {"item-1"}
            async with manager.session_factory() as session:
                decision = await session.scalar(
                    select(WebSyncProductIdentityDecisionModel).where(
                        WebSyncProductIdentityDecisionModel.tenant_id == tenant_id,
                        WebSyncProductIdentityDecisionModel.job_id == job.job_id,
                    )
                )
                assert decision is not None
                assert decision.winner_item_id == "item-1"
                assert decision.state == "promoted"
                assert decision.decision_revision == 2
                promotion_event = await session.scalar(
                    select(AuditEventModel).where(
                        AuditEventModel.tenant_id == tenant_id,
                        AuditEventModel.resource_id == job.job_id,
                        AuditEventModel.event_type
                        == "knowledge.web_sync.product_identity_promoted",
                    )
                )
                assert promotion_event is not None
                assert promotion_event.details["previous_winner_item_id"] == "item-0"
                assert promotion_event.details["winner_item_id"] == "item-1"
    finally:
        with tenant_scope(tenant_id):
            async with manager.session_factory.begin() as session:
                await session.execute(
                    delete(AuditEventModel).where(AuditEventModel.tenant_id == tenant_id)
                )
                await session.execute(
                    delete(WebSyncJobModel).where(WebSyncJobModel.tenant_id == tenant_id)
                )
                await session.execute(
                    delete(SupportSiteModel).where(SupportSiteModel.tenant_id == tenant_id)
                )
                await session.execute(delete(TenantModel).where(TenantModel.tenant_id == tenant_id))
        await manager.dispose()


async def test_production_finalization_is_recoverable_and_cannot_be_canceled() -> None:
    tenant_id = f"test-{uuid4()}"
    site_id = f"site-{uuid4()}"
    now = datetime.now(UTC)
    manager = DatabaseSessionManager(DATABASE_URL)
    store = PostgreSQLWebSyncJobStore(manager.session_factory)
    job = replace(_job(tenant_id, site_id, "production-finalization", now), max_attempts=1)
    try:
        with tenant_scope(tenant_id):
            async with manager.session_factory.begin() as session:
                session.add_all(
                    (
                        _tenant(tenant_id, now),
                        SupportSiteModel(
                            tenant_id=tenant_id,
                            site_id=site_id,
                            public_widget_id=f"site_pub_{uuid4().hex}",
                            name="Production finalization",
                            base_url="https://shop.example.com",
                            allowed_origins=["https://shop.example.com"],
                            widget_daily_message_limit=100,
                            primary_language="en",
                            status="active",
                            created_at=now,
                            updated_at=now,
                        ),
                    )
                )
            await store.enqueue(job)
            claimed = await store.claim_next(
                tenant_id=tenant_id,
                worker_id="worker-1",
                claimed_at=now,
                lease_expires_at=now + timedelta(seconds=10),
            )
            assert claimed is not None and claimed.attempt_count == 1
            finalizing = await store.start_finalization(
                tenant_id=tenant_id,
                job_id=job.job_id,
                worker_id="worker-1",
                started_at=now + timedelta(seconds=1),
            )
            assert finalizing.phase is WebSyncJobPhase.FINALIZING

            with pytest.raises(ValueError, match="cannot be canceled"):
                await store.request_cancel(
                    tenant_id=tenant_id,
                    job_id=job.job_id,
                    requested_by="admin-1",
                    requested_at=now + timedelta(seconds=2),
                )

            recovered = await store.claim_next(
                tenant_id=tenant_id,
                worker_id="worker-2",
                claimed_at=now + timedelta(seconds=11),
                lease_expires_at=now + timedelta(minutes=2),
            )
            assert recovered is not None
            assert recovered.phase is WebSyncJobPhase.FINALIZING
            assert recovered.attempt_count == 2
    finally:
        with tenant_scope(tenant_id):
            async with manager.session_factory.begin() as session:
                await session.execute(
                    delete(AuditEventModel).where(AuditEventModel.tenant_id == tenant_id)
                )
                await session.execute(
                    delete(WebSyncJobModel).where(WebSyncJobModel.tenant_id == tenant_id)
                )
                await session.execute(
                    delete(SupportSiteModel).where(SupportSiteModel.tenant_id == tenant_id)
                )
                await session.execute(delete(TenantModel).where(TenantModel.tenant_id == tenant_id))
        await manager.dispose()


def _job(tenant_id: str, site_id: str, key: str, requested_at: datetime) -> WebSyncJob:
    return WebSyncJob(
        tenant_id=tenant_id,
        job_id=str(uuid4()),
        site_id=site_id,
        base_url="https://shop.example.com",
        status=WebSyncJobStatus.QUEUED,
        trigger=WebSyncTrigger.MANUAL,
        mode=WebSyncMode.PRODUCTION,
        publication_status=WebSyncPublicationStatus.PENDING,
        manifest_id="",
        sample_size=None,
        policy=WebSyncPolicySnapshot(max_pages=500),
        requested_by="admin-1",
        correlation_id=str(uuid4()),
        idempotency_key=key,
        requested_at=requested_at,
    )


def _tenant(tenant_id: str, created_at: datetime) -> TenantModel:
    return TenantModel(
        tenant_id=tenant_id,
        name=f"Web Sync Workspace {tenant_id}",
        status="active",
        created_at=created_at,
        updated_at=created_at,
    )


async def _assert_job_and_page_snapshot(
    store: PostgreSQLWebSyncJobStore,
    *,
    tenant_id: str,
    job_id: str,
    job_snapshot: tuple[int, int, int, int, int, int],
    page_snapshot: tuple[
        WebSyncJobItemStatus,
        int,
        str | None,
        datetime | None,
        datetime | None,
        str | None,
    ],
) -> None:
    current_job = await store.get(tenant_id=tenant_id, job_id=job_id)
    current_pages = await store.list_items(
        tenant_id=tenant_id,
        job_id=job_id,
        status=None,
        limit=10,
    )
    assert current_job is not None
    assert len(current_pages) == 1
    current_page = current_pages[0]
    assert (
        current_job.completed_count,
        current_job.succeeded_count,
        current_job.not_modified_count,
        current_job.excluded_item_count,
        current_job.failed_item_count,
        current_job.state_version,
    ) == job_snapshot
    assert (
        current_page.status,
        current_page.attempt_count,
        current_page.lease_owner,
        current_page.lease_expires_at,
        current_page.completed_at,
        current_page.error_code,
    ) == page_snapshot


def _report() -> WebSyncJobReport:
    return WebSyncJobReport(
        pipeline_sync_job_id="pipeline-1",
        published=True,
        discovered_count=100,
        document_count=100,
        changed_document_count=0,
        unchanged_document_count=100,
        http_not_modified_count=100,
        duplicate_count=0,
        duplicate_product_count=1,
        product_count=99,
        pending_removal_count=0,
        expired_count=0,
        indexed_chunk_count=0,
        excluded_count=0,
        failed_count=0,
        errors={},
    )
