from dataclasses import replace
from datetime import UTC, datetime

from app.application.dto import AnswerKnowledgeCommand
from app.application.services import AnswerKnowledgeService, ProductCatalogAnswerService
from app.domain.models import AuthenticatedPrincipal, KnowledgeEvidence, ProductSnapshot
from app.domain.ports import KnowledgeQuery, KnowledgeSitePublicationState
from app.integrations.retrieval import SitePublicationGatedKnowledgeRetriever
from tests.fakes.adapters import (
    GroundedChatModel,
    InMemoryKnowledgeControlPlane,
    InMemoryKnowledgeRetriever,
    InMemoryProductCatalog,
)


def _query() -> KnowledgeQuery:
    return KnowledgeQuery(
        tenant_id="tenant-a",
        text="What is SKU-1?",
        audience="public",
        language="en",
        filters={"site_id": "site-a"},
    )


def _evidence() -> KnowledgeEvidence:
    return KnowledgeEvidence(
        chunk_id="chunk-a",
        document_id="document-a",
        text="SKU-1 is an example product.",
        score=0.9,
        source="https://shop.example.test/product.html",
        metadata={"tenant_id": "tenant-a", "site_id": "site-a"},
    )


async def test_publication_gate_only_delegates_for_active_site() -> None:
    control_plane = InMemoryKnowledgeControlPlane()
    delegate = InMemoryKnowledgeRetriever((_evidence(),))
    retriever = SitePublicationGatedKnowledgeRetriever(delegate, control_plane)

    assert await retriever.search(_query()) == [_evidence()]
    assert delegate.queries == [_query()]

    control_plane.publication_states[("tenant-a", "site-a")] = KnowledgeSitePublicationState(
        state="switching",
        active_publication_id="publication-a",
        pending_publication_id="publication-b",
    )
    delegate.queries.clear()

    assert await retriever.search(_query()) == []
    assert delegate.queries == []


async def test_answer_service_blocks_catalog_facts_while_site_is_switching() -> None:
    control_plane = InMemoryKnowledgeControlPlane()
    control_plane.publication_states[("tenant-a", "site-a")] = KnowledgeSitePublicationState(
        state="switching",
        active_publication_id="publication-a",
        pending_publication_id="publication-b",
    )
    retriever = InMemoryKnowledgeRetriever((_evidence(),))
    product = ProductSnapshot(
        tenant_id="tenant-a",
        site_id="site-a",
        snapshot_id="publication-a",
        product_key="SKU-1",
        sku="SKU-1",
        mpn="SKU-1",
        name="Example Product",
        canonical_url="https://shop.example.test/product.html",
        fetched_at=datetime.now(UTC),
        content_hash="hash-a",
        source_url="https://shop.example.test/product.html",
    )
    service = AnswerKnowledgeService(
        retriever=retriever,
        chat_model=GroundedChatModel(),
        control_plane=control_plane,
        product_catalog=ProductCatalogAnswerService(InMemoryProductCatalog((product,))),
    )
    principal = AuthenticatedPrincipal(
        subject_id="customer-a",
        tenant_id="tenant-a",
        roles=frozenset({"customer"}),
        scopes=frozenset({"knowledge:read"}),
        authentication_method="public_widget_token",
        authenticated_at=datetime.now(UTC),
        correlation_id="correlation-a",
        site_id="site-a",
    )

    result = await service.execute(
        AnswerKnowledgeCommand(
            principal=replace(principal),
            question="What is the price of SKU-1?",
            language="en",
        )
    )

    assert result.status == "clarification"
    assert result.retrieval_degraded is True
    assert "currently being updated" in (result.message or "")
    assert result.evidence == ()
    assert retriever.queries == []
