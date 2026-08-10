from app.domain.models import KnowledgeEvidence
from app.domain.ports import KnowledgeQuery
from app.integrations.retrieval import (
    DeterministicKnowledgeReranker,
    HashingSparseEmbeddingProvider,
)


async def test_sparse_embeddings_are_deterministic_and_term_sensitive() -> None:
    provider = HashingSparseEmbeddingProvider()

    first, second, different = await provider.embed_sparse(
        ["SKU-X100 无法启动", "SKU-X100 无法启动", "退款政策"]
    )

    assert first == second
    assert first.indices
    assert set(first.indices) != set(different.indices)


async def test_reranker_prefers_exact_tenant_product_evidence() -> None:
    query = KnowledgeQuery(
        tenant_id="tenant-a",
        text="SKU-X100 无法启动",
        audience="public",
        language="zh-CN",
        limit=2,
        score_threshold=0.0,
    )
    candidates = [
        KnowledgeEvidence(
            chunk_id="global",
            document_id="global-doc",
            text="产品问题请参考正式说明书。",
            score=0.35,
            source="global.md",
            metadata={
                "partition_id": "__global__",
                "tenant_id": "__global__",
                "authority_level": 100,
                "priority": 100,
            },
        ),
        KnowledgeEvidence(
            chunk_id="tenant",
            document_id="tenant-doc",
            text="SKU-X100 无法启动时，请长按电源键五秒。",
            score=0.65,
            source="product.md",
            metadata={
                "partition_id": "tenant-a",
                "tenant_id": "tenant-a",
                "authority_level": 80,
                "priority": 80,
            },
        ),
    ]

    ranked = await DeterministicKnowledgeReranker().rerank(
        query=query,
        candidates=candidates,
        limit=2,
    )

    assert [item.chunk_id for item in ranked] == ["tenant", "global"]
    assert ranked[0].metadata["lexical_score"] > ranked[1].metadata["lexical_score"]


async def test_reranker_rejects_irrelevant_low_confidence_candidate() -> None:
    query = KnowledgeQuery(
        tenant_id="tenant-a",
        text="SKU-X100 无法启动",
        audience="public",
        language="zh-CN",
    )
    candidate = KnowledgeEvidence(
        chunk_id="irrelevant",
        document_id="other",
        text="公司节假日安排。",
        score=0.1,
        source="calendar.md",
        metadata={
            "partition_id": "__global__",
            "tenant_id": "__global__",
            "authority_level": 10,
            "priority": 10,
        },
    )

    ranked = await DeterministicKnowledgeReranker().rerank(
        query=query,
        candidates=[candidate],
        limit=5,
    )

    assert ranked == []


async def test_reranker_preserves_exact_sku_across_verbose_chinese_query() -> None:
    query = KnowledgeQuery(
        tenant_id="tenant-a",
        text="SKU-103 可以送往中国吗？",
        audience="public",
        language="zh-CN",
    )
    candidate = KnowledgeEvidence(
        chunk_id="product",
        document_id="product-doc",
        text="Model SKU-103 ships within the United States only.",
        score=0.65,
        source="product.html",
        metadata={
            "partition_id": "tenant-a",
            "tenant_id": "tenant-a",
            "authority_level": 30,
            "priority": 50,
        },
    )

    ranked = await DeterministicKnowledgeReranker().rerank(
        query=query,
        candidates=[candidate],
        limit=5,
    )

    assert ranked[0].score >= query.score_threshold
    assert ranked[0].metadata["lexical_score"] == 0.7


async def test_reranker_accepts_strong_cross_language_semantic_candidate() -> None:
    query = KnowledgeQuery(
        tenant_id="tenant-a",
        text="Welche Puppe ist für eine leichtere Handhabung geeignet?",
        audience="public",
        language="de",
    )
    candidate = KnowledgeEvidence(
        chunk_id="lightweight-product",
        document_id="product-doc",
        text="This portable display stand is 125 cm tall and weighs 17.5 kg.",
        score=0.65,
        source="https://shop.example.test/lightweight.html",
        metadata={
            "partition_id": "tenant-a",
            "tenant_id": "tenant-a",
            "authority_level": 30,
            "priority": 50,
        },
    )

    ranked = await DeterministicKnowledgeReranker().rerank(
        query=query,
        candidates=[candidate],
        limit=5,
    )

    assert ranked[0].score >= query.score_threshold
    assert ranked[0].metadata["lexical_score"] == 0.0
