import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from qdrant_client import AsyncQdrantClient
from sqlalchemy import delete

from app.integrations.postgres import (
    DatabaseSessionManager,
    PostgreSQLKnowledgeControlPlaneAdapter,
    PostgreSQLProductCatalogAdapter,
)
from app.integrations.postgres.models import (
    AuditEventModel,
    KnowledgeChunkManifestModel,
    KnowledgeDocumentModel,
    KnowledgeDocumentVersionModel,
    KnowledgeLinkModel,
    KnowledgeSyncJobModel,
    ProductCatalogSnapshotModel,
    ProductFactSnapshotModel,
    SupportSiteModel,
    TenantModel,
)
from app.integrations.qdrant import QdrantKnowledgeAdapter
from app.integrations.retrieval import (
    DeterministicKnowledgeReranker,
    HashingSparseEmbeddingProvider,
)
from app.knowledge import MarkdownChunker
from app.knowledge.web import WebCrawlPolicy, WebKnowledgeSyncService
from app.knowledge.web.models import StructuredWebDocument, WebCrawlResult
from tests.fakes.adapters import DeterministicEmbeddingProvider

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION_TESTS") != "1",
        reason="set RUN_INTEGRATION_TESTS=1 with PostgreSQL and Qdrant running",
    ),
]

DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/customer_agent",
)
QDRANT_URL = os.getenv("TEST_QDRANT_URL", "http://localhost:6333")


class SingleDocumentCrawler:
    async def crawl(self, policy: WebCrawlPolicy) -> WebCrawlResult:
        url = policy.seed_urls[0] if policy.seed_urls else "https://shop.example.test/product.html"
        return WebCrawlResult(
            discovered_count=1,
            documents=(
                StructuredWebDocument(
                    tenant_id=policy.tenant_id,
                    site_id=policy.site_id,
                    document_id="document-product-1",
                    canonical_url=url,
                    title="Production Product",
                    language="en",
                    body=(
                        "# Production Product\n\n"
                        "A detailed product description with material, dimensions, care, shipping, "
                        "and support information suitable for customer answers. It includes "
                        "cleaning guidance, storage precautions, warehouse dispatch details, "
                        "delivery expectations, warranty boundaries, and reviewed specifications."
                    ),
                    content_hash="source-hash",
                    category="product",
                    product={
                        "name": "Production Product",
                        "sku": "SKU-PRODUCTION-1",
                        "brand": "Example Brand",
                        "offers": {"price": "599", "priceCurrency": "USD"},
                    },
                    internal_links=(),
                    fetched_at=datetime.now(UTC),
                    source_metadata={
                        "requested_url": url,
                        "final_url": url,
                        "etag": '"production-v1"',
                    },
                ),
            ),
            excluded_count=0,
            failed_count=0,
            errors={},
        )


async def test_real_staged_web_publication_reconciles_and_activates() -> None:
    tenant_id = f"test-{uuid4()}"
    site_id = "site-a"
    snapshot_id = str(uuid4())
    collection_name = f"test_staged_web_{uuid4().hex}"
    manager = DatabaseSessionManager(DATABASE_URL)
    control_plane = PostgreSQLKnowledgeControlPlaneAdapter(manager.session_factory)
    product_catalog = PostgreSQLProductCatalogAdapter(manager.session_factory)
    dense_provider = DeterministicEmbeddingProvider()
    sparse_provider = HashingSparseEmbeddingProvider()
    indexer = QdrantKnowledgeAdapter(
        url=QDRANT_URL,
        collection_name=collection_name,
        embedding_provider=dense_provider,
        sparse_embedding_provider=sparse_provider,
        reranker=DeterministicKnowledgeReranker(),
        vector_size=2,
    )
    service = WebKnowledgeSyncService(
        crawler=SingleDocumentCrawler(),  # type: ignore[arg-type]
        chunker=MarkdownChunker(max_characters=200),
        embedding_provider=dense_provider,
        sparse_embedding_provider=sparse_provider,
        indexer=indexer,
        control_plane=control_plane,
        product_catalog=product_catalog,
        publication_committer=control_plane,
        index_namespace=f"{collection_name}:v1",
    )
    policy = WebCrawlPolicy(
        tenant_id=tenant_id,
        site_id=site_id,
        base_url="https://shop.example.test",
        seed_urls=("https://shop.example.test/product.html",),
        follow_internal_links=False,
        discover_sitemaps=False,
        crawl_delay_seconds=0,
    )

    try:
        async with manager.session_factory.begin() as session:
            session.add_all(
                (
                    TenantModel(
                        tenant_id=tenant_id,
                        name="Staged Publication Workspace",
                        status="active",
                        created_at=datetime.now(UTC),
                        updated_at=datetime.now(UTC),
                    ),
                    SupportSiteModel(
                        tenant_id=tenant_id,
                        site_id=site_id,
                        public_widget_id=f"widget-{uuid4().hex}",
                        name="Integration Site",
                        base_url="https://shop.example.test",
                        allowed_origins=["https://shop.example.test"],
                        status="active",
                    ),
                )
            )
        await service.begin_staged_sync(policy, snapshot_id=snapshot_id)
        report = await service.stage_page(policy, snapshot_id=snapshot_id)
        version_id = report.validators[0].version_id
        activation = await service.publish_staged_sync(
            policy,
            snapshot_id=snapshot_id,
            active_version_ids=(version_id,),
            staged_version_ids=(version_id,),
            expected_chunk_count=report.indexed_count,
            expected_product_count=report.product_count,
            discovered_count=report.discovered_count,
            errors=report.errors,
        )

        assert report.indexed_count > 0
        assert activation.activated_count == 1
        assert (
            await indexer.count_snapshot_points(
                tenant_id=tenant_id,
                site_id=site_id,
                snapshot_id=snapshot_id,
            )
            == report.indexed_count
        )
        assert await control_plane.list_active_site_version_ids(
            tenant_id=tenant_id,
            site_id=site_id,
        ) == (version_id,)
        assert (
            await product_catalog.count_staged_products(
                tenant_id=tenant_id,
                site_id=site_id,
                snapshot_id=snapshot_id,
            )
            == 1
        )
        publication = await control_plane.get_site_publication_state(
            tenant_id=tenant_id,
            site_id=site_id,
        )
        assert publication.state == "active"
        assert publication.active_publication_id == snapshot_id
        assert publication.pending_publication_id is None
    finally:
        await indexer.close()
        qdrant = AsyncQdrantClient(
            url=QDRANT_URL,
            timeout=30.0,
            check_compatibility=False,
            trust_env=False,
        )
        try:
            if await qdrant.collection_exists(collection_name):
                await qdrant.delete_collection(collection_name)
        finally:
            await qdrant.close()
        async with manager.session_factory.begin() as session:
            for model in (
                ProductFactSnapshotModel,
                ProductCatalogSnapshotModel,
                KnowledgeLinkModel,
                KnowledgeChunkManifestModel,
                KnowledgeDocumentVersionModel,
                KnowledgeDocumentModel,
                KnowledgeSyncJobModel,
                AuditEventModel,
                SupportSiteModel,
                TenantModel,
            ):
                await session.execute(delete(model).where(model.tenant_id == tenant_id))
        await manager.dispose()
