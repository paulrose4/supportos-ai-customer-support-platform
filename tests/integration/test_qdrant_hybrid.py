import os
from uuid import uuid4

import pytest
from qdrant_client import AsyncQdrantClient, QdrantClient, models

from app.domain.ports import KnowledgeChunk, KnowledgeQuery
from app.integrations.qdrant import QdrantKnowledgeAdapter
from app.integrations.retrieval import (
    DeterministicKnowledgeReranker,
    HashingSparseEmbeddingProvider,
)
from tests.fakes.adapters import DeterministicEmbeddingProvider

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION_TESTS") != "1",
        reason="set RUN_INTEGRATION_TESTS=1 with Qdrant running",
    ),
]

QDRANT_URL = os.getenv("TEST_QDRANT_URL", "http://localhost:6333")


@pytest.fixture(scope="module")
def hybrid_collection_name() -> str:
    collection_name = f"test_hybrid_{uuid4().hex}"
    yield collection_name
    client = QdrantClient(
        url=QDRANT_URL,
        timeout=60.0,
        check_compatibility=False,
        trust_env=False,
    )
    try:
        if client.collection_exists(collection_name):
            client.delete_collection(collection_name)
    finally:
        client.close()


async def test_hybrid_search_combines_global_and_tenant_without_cross_tenant_leakage(
    hybrid_collection_name: str,
) -> None:
    dense_provider = DeterministicEmbeddingProvider()
    sparse_provider = HashingSparseEmbeddingProvider()
    adapter = QdrantKnowledgeAdapter(
        url=QDRANT_URL,
        collection_name=hybrid_collection_name,
        embedding_provider=dense_provider,
        sparse_embedding_provider=sparse_provider,
        reranker=DeterministicKnowledgeReranker(),
        vector_size=2,
        tenant_candidate_limit=10,
        global_candidate_limit=10,
        rerank_candidate_limit=10,
    )
    try:
        texts = [
            "SKU-X100 无法启动时，请长按电源键五秒。",
            "查询订单和账户数据前必须完成可信身份验证。",
            "其他租户的秘密型号是 OTHER-900。",
        ]
        dense_vectors = await dense_provider.embed(texts)
        sparse_vectors = await sparse_provider.embed_sparse(texts)
        await adapter.upsert(
            [
                _chunk(
                    tenant_id="tenant-a",
                    text=texts[0],
                    dense_vector=dense_vectors[0],
                    sparse_vector=sparse_vectors[0],
                    scope="tenant",
                ),
                _chunk(
                    tenant_id="__global__",
                    text=texts[1],
                    dense_vector=dense_vectors[1],
                    sparse_vector=sparse_vectors[1],
                    scope="global",
                ),
                _chunk(
                    tenant_id="tenant-b",
                    text=texts[2],
                    dense_vector=dense_vectors[2],
                    sparse_vector=sparse_vectors[2],
                    scope="tenant",
                ),
            ]
        )

        tenant_results = await adapter.search(
            KnowledgeQuery(
                tenant_id="tenant-a",
                text="SKU-X100 无法启动",
                audience="public",
                language="zh-CN",
                score_threshold=0.2,
            )
        )
        global_results = await adapter.search(
            KnowledgeQuery(
                tenant_id="tenant-a",
                text="为什么查询订单前必须验证身份",
                audience="public",
                language="zh-CN",
                score_threshold=0.2,
            )
        )

        assert tenant_results[0].metadata["tenant_id"] == "tenant-a"
        assert any(item.metadata["tenant_id"] == "__global__" for item in global_results)
        assert all(item.metadata["tenant_id"] != "tenant-b" for item in tenant_results)
        assert all(item.metadata["tenant_id"] != "tenant-b" for item in global_results)
    finally:
        await adapter.close()
        await _clear_collection_points(hybrid_collection_name)


async def test_site_search_supports_cross_language_queries_without_cross_site_leakage(
    hybrid_collection_name: str,
) -> None:
    dense_provider = DeterministicEmbeddingProvider()
    sparse_provider = HashingSparseEmbeddingProvider()
    adapter = QdrantKnowledgeAdapter(
        url=QDRANT_URL,
        collection_name=hybrid_collection_name,
        embedding_provider=dense_provider,
        sparse_embedding_provider=sparse_provider,
        reranker=DeterministicKnowledgeReranker(),
        vector_size=2,
        tenant_candidate_limit=10,
        global_candidate_limit=10,
        rerank_candidate_limit=10,
    )
    try:
        texts = [
            "Model SKU-103 costs 299 USD and ships within the United States only.",
            "Model OTHER-900 costs 999 USD and belongs to another site.",
        ]
        dense_vectors = await dense_provider.embed(texts)
        sparse_vectors = await sparse_provider.embed_sparse(texts)
        await adapter.upsert(
            [
                _chunk(
                    tenant_id="tenant-a",
                    text=texts[0],
                    dense_vector=dense_vectors[0],
                    sparse_vector=sparse_vectors[0],
                    scope="site",
                    site_id="site-a",
                    language="en",
                ),
                _chunk(
                    tenant_id="tenant-a",
                    text=texts[1],
                    dense_vector=dense_vectors[1],
                    sparse_vector=sparse_vectors[1],
                    scope="site",
                    site_id="site-b",
                    language="en",
                ),
            ]
        )

        results = await adapter.search(
            KnowledgeQuery(
                tenant_id="tenant-a",
                text="SKU-103 多少钱？",
                audience="public",
                language="zh-CN",
                score_threshold=0.2,
                filters={"site_id": "site-a"},
            )
        )

        assert results
        assert all(item.metadata["site_id"] == "site-a" for item in results)
        assert all(item.metadata["language"] == "en" for item in results)
    finally:
        await adapter.close()
        await _clear_collection_points(hybrid_collection_name)


async def test_site_search_filters_product_by_requested_url_alias(
    hybrid_collection_name: str,
) -> None:
    dense_provider = DeterministicEmbeddingProvider()
    sparse_provider = HashingSparseEmbeddingProvider()
    adapter = QdrantKnowledgeAdapter(
        url=QDRANT_URL,
        collection_name=hybrid_collection_name,
        embedding_provider=dense_provider,
        sparse_embedding_provider=sparse_provider,
        reranker=DeterministicKnowledgeReranker(),
        vector_size=2,
        tenant_candidate_limit=10,
        global_candidate_limit=10,
        rerank_candidate_limit=10,
    )
    texts = [
        "OVE01-21 is a 125cm TPE model with reviewed product details.",
        "OTHER-900 is another TPE model with different product details.",
    ]
    try:
        dense_vectors = await dense_provider.embed(texts)
        sparse_vectors = await sparse_provider.embed_sparse(texts)
        await adapter.upsert(
            [
                _chunk(
                    tenant_id="tenant-a",
                    text=texts[0],
                    dense_vector=dense_vectors[0],
                    sparse_vector=sparse_vectors[0],
                    scope="site",
                    site_id="site-a",
                    metadata={
                        "category": "product",
                        "requested_url": "https://shop.example/agent-test.html?121=",
                        "requested_path": "/agent-test.html",
                        "canonical_url": "https://shop.example/products/ove01-21.html",
                        "canonical_path": "/products/ove01-21.html",
                        "product": {"sku": "OVE01-21"},
                    },
                ),
                _chunk(
                    tenant_id="tenant-a",
                    text=texts[1],
                    dense_vector=dense_vectors[1],
                    sparse_vector=sparse_vectors[1],
                    scope="site",
                    site_id="site-a",
                    metadata={
                        "category": "product",
                        "requested_path": "/products/other.html",
                        "product": {"sku": "OTHER-900"},
                    },
                ),
            ]
        )

        results = await adapter.search(
            KnowledgeQuery(
                tenant_id="tenant-a",
                text="How should I care for this TPE product?",
                audience="public",
                language="en",
                score_threshold=0.0,
                filters={"site_id": "site-a", "source_paths": ("/agent-test.html",)},
            )
        )

        assert [item.text for item in results] == [texts[0]]
    finally:
        await adapter.close()
        await _clear_collection_points(hybrid_collection_name)


async def test_snapshot_point_count_is_scoped_by_tenant_site_and_snapshot(
    hybrid_collection_name: str,
) -> None:
    dense_provider = DeterministicEmbeddingProvider()
    sparse_provider = HashingSparseEmbeddingProvider()
    adapter = QdrantKnowledgeAdapter(
        url=QDRANT_URL,
        collection_name=hybrid_collection_name,
        embedding_provider=dense_provider,
        sparse_embedding_provider=sparse_provider,
        reranker=DeterministicKnowledgeReranker(),
        vector_size=2,
    )
    texts = ["first staged point", "other site staged point"]
    try:
        dense_vectors = await dense_provider.embed(texts)
        sparse_vectors = await sparse_provider.embed_sparse(texts)
        await adapter.upsert(
            [
                _chunk(
                    tenant_id="tenant-a",
                    text=texts[0],
                    dense_vector=dense_vectors[0],
                    sparse_vector=sparse_vectors[0],
                    scope="site",
                    site_id="site-a",
                    metadata={
                        "source_type": "website_html",
                        "snapshot_id": "snapshot-a",
                        "is_active": False,
                    },
                ),
                _chunk(
                    tenant_id="tenant-a",
                    text=texts[1],
                    dense_vector=dense_vectors[1],
                    sparse_vector=sparse_vectors[1],
                    scope="site",
                    site_id="site-b",
                    metadata={
                        "source_type": "website_html",
                        "snapshot_id": "snapshot-a",
                        "is_active": False,
                    },
                ),
            ]
        )

        assert (
            await adapter.count_snapshot_points(
                tenant_id="tenant-a",
                site_id="site-a",
                snapshot_id="snapshot-a",
            )
            == 1
        )
        assert (
            await adapter.count_snapshot_points(
                tenant_id="tenant-b",
                site_id="site-a",
                snapshot_id="snapshot-a",
            )
            == 0
        )
    finally:
        await adapter.close()
        await _clear_collection_points(hybrid_collection_name)


async def _clear_collection_points(collection_name: str) -> None:
    client = AsyncQdrantClient(
        url=QDRANT_URL,
        timeout=30.0,
        check_compatibility=False,
        trust_env=False,
    )
    try:
        if await client.collection_exists(collection_name):
            await client.delete(
                collection_name=collection_name,
                points_selector=models.FilterSelector(filter=models.Filter()),
                wait=True,
            )
    finally:
        await client.close()


def _chunk(
    *,
    tenant_id: str,
    text: str,
    dense_vector: list[float],
    sparse_vector,
    scope: str,
    site_id: str | None = None,
    language: str = "zh-CN",
    metadata: dict | None = None,
) -> KnowledgeChunk:
    identifier = str(uuid4())
    return KnowledgeChunk(
        tenant_id=tenant_id,
        chunk_id=identifier,
        document_id=f"document-{identifier}",
        version_id=f"version-{identifier}",
        text=text,
        vector=dense_vector,
        sparse_vector=sparse_vector,
        metadata={
            "knowledge_scope": scope,
            "status": "published",
            "audience": "public",
            "language": language,
            "source": f"{identifier}.md",
            "source_type": "integration_test",
            "authority_level": 80,
            "priority": 80,
            "product": "all",
            "region": "global",
            **(metadata or {}),
            **({"site_id": site_id} if site_id else {}),
        },
    )
