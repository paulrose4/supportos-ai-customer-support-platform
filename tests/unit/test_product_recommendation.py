from dataclasses import replace
from datetime import UTC, datetime

from app.application.services import ProductRecommendationService
from app.domain.models import (
    CandidateReview,
    MemoryFactStatus,
    ProductSnapshot,
    RecommendationPlannerAdvice,
    RecommendationSoftPreference,
    SalesMemoryFact,
)
from app.domain.ports import ProductRetrievalHit
from app.domain.rules import (
    build_product_recommendation_plan,
    product_passes_hard_constraints,
    reciprocal_rank_fusion,
    structured_product_similarity,
)
from app.integrations.retrieval import FastEmbedProductReranker
from tests.fakes.adapters import InMemoryProductCatalog

NOW = datetime(2026, 7, 27, tzinfo=UTC)


def product(
    sku: str,
    *,
    price: str | None = "500",
    currency: str | None = "USD",
    material: str | None = "TPE",
    weight: str | None = "20 kg",
    height: str | None = "160 cm",
    stock: str | None = "https://schema.org/InStock",
) -> ProductSnapshot:
    return ProductSnapshot(
        tenant_id="tenant-a",
        site_id="site-a",
        snapshot_id="snapshot-1",
        product_key=sku,
        sku=sku,
        name=f"Product {sku}",
        canonical_url=f"https://shop.example.test/products/{sku.casefold()}",
        material=material,
        dimensions={"Height": height} if height else {},
        weight=weight,
        price=price,
        currency=currency,
        stock_status=stock,
        fetched_at=NOW,
        content_hash=f"hash-{sku}",
        source_url=f"https://shop.example.test/products/{sku.casefold()}",
    )


async def test_exact_mode_enforces_budget_currency_and_required_facts() -> None:
    valid = product("VALID", price="299", currency="USD")
    wrong_currency = product("EUR", price="199", currency="EUR")
    missing_price = product("UNKNOWN", price=None, currency=None)
    service = ProductRecommendationService(
        InMemoryProductCatalog((valid, wrong_currency, missing_price))
    )

    result = await service.recommend(
        tenant_id="tenant-a",
        site_id="site-a",
        question="Recommend a TPE model under $300",
        now=NOW,
    )

    assert result.mode.value == "exact_match"
    assert [item.product.sku for item in result.candidates] == ["VALID"]
    assert "price<=300" in result.hard_constraint_labels
    assert "currency=USD" in result.hard_constraint_labels


async def test_exact_mode_does_not_relax_unknown_weight_requirement() -> None:
    unknown = product("UNKNOWN", weight=None)
    service = ProductRecommendationService(InMemoryProductCatalog((unknown,)))

    result = await service.recommend(
        tenant_id="tenant-a",
        site_id="site-a",
        question="Recommend one under 15 kg",
        now=NOW,
    )

    assert result.candidates == ()
    assert result.no_match_reason == "strict_constraints_require_missing_product_facts"
    assert result.warnings == ("missing_required_fact:weight",)


async def test_soft_material_preference_ranks_without_excluding_alternatives() -> None:
    tpe = product("TPE", material="TPE", weight="15 kg")
    silicone = product("SILICONE", material="Silicone", weight="15 kg")
    service = ProductRecommendationService(InMemoryProductCatalog((tpe, silicone)))

    result = await service.recommend(
        tenant_id="tenant-a",
        site_id="site-a",
        question="I prefer silicone if possible",
        now=NOW,
    )

    assert result.mode.value == "preference"
    assert [item.product.sku for item in result.candidates] == ["SILICONE", "TPE"]
    assert "material_match" in result.candidates[0].match_reasons


def test_hard_constraints_are_not_changed_by_memory_preferences() -> None:
    plan = build_product_recommendation_plan(
        "I need TPE under $400",
        memory_facts=(SalesMemoryFact("material", "SILICONE", MemoryFactStatus.CONFIRMED, 1),),
    )

    accepted, violations, missing = product_passes_hard_constraints(
        product("SILICONE", price="300", material="Silicone"),
        plan,
    )

    assert not accepted
    assert violations == ("material",)
    assert missing == ()
    assert [item.value for item in plan.constraints if item.field == "material"] == ["TPE"]


def test_structured_similarity_penalizes_sparse_candidate_coverage() -> None:
    source = product("SOURCE", price="500", weight="20 kg", height="160 cm")
    complete = product("COMPLETE", price="500", weight="20 kg", height="160 cm")
    sparse = replace(
        complete,
        product_key="SPARSE",
        sku="SPARSE",
        material=None,
        weight=None,
        dimensions={},
        price=None,
        currency=None,
    )

    complete_score, complete_coverage, _matched, _missing = structured_product_similarity(
        source,
        complete,
    )
    sparse_score, sparse_coverage, _matched, _missing = structured_product_similarity(
        source,
        sparse,
    )

    assert complete_coverage > sparse_coverage
    assert complete_score > sparse_score


def test_weight_limit_is_not_misparsed_as_a_price_budget() -> None:
    plan = build_product_recommendation_plan("Recommend one under 15 kg")

    fields = {item.field for item in plan.constraints}
    assert "max_weight_kg" in fields
    assert "max_price" not in fields


def test_one_sided_height_limit_is_a_hard_constraint() -> None:
    plan = build_product_recommendation_plan("I need one at least 150 cm")

    assert any(
        item.field == "min_height_cm" and item.value == "150" for item in plan.hard_constraints
    )


def test_rrf_ignores_channels_without_any_signal() -> None:
    without_zero_channel = reciprocal_rank_fusion(([0.1, 0.9],))
    with_zero_channel = reciprocal_rank_fusion(([0.1, 0.9], [0.0, 0.0]))

    assert with_zero_channel == without_zero_channel


class _Planner:
    async def plan(self, **_values):  # type: ignore[no-untyped-def]
        return RecommendationPlannerAdvice(
            soft_preferences=(RecommendationSoftPreference("material", "Silicone", 1.0),)
        )


class _InvalidEvidenceReviewer:
    async def review(self, **values):  # type: ignore[no-untyped-def]
        candidate = values["candidates"][0]
        return (
            CandidateReview(
                candidate_key=candidate.candidate_key,
                relevant=True,
                score=1.0,
                reason="unsupported assertion",
                evidence_field_ids=("invented_field",),
            ),
        )


async def test_planner_can_only_add_soft_preferences() -> None:
    silicone = product("SILICONE", material="Silicone")
    tpe = product("TPE", material="TPE")
    service = ProductRecommendationService(
        InMemoryProductCatalog((tpe, silicone)),
        planner=_Planner(),
    )

    result = await service.recommend(
        tenant_id="tenant-a",
        site_id="site-a",
        question="I want a natural-feeling option",
        now=NOW,
    )

    assert [item.product.sku for item in result.candidates] == ["SILICONE", "TPE"]
    assert result.mode.value == "preference"


async def test_reviewer_evidence_must_exist_in_candidate_payload() -> None:
    service = ProductRecommendationService(
        InMemoryProductCatalog((product("ONE"),)),
        reviewer=_InvalidEvidenceReviewer(),
    )

    result = await service.recommend(
        tenant_id="tenant-a",
        site_id="site-a",
        question="Recommend one option",
        now=NOW,
    )

    assert "candidate_review_evidence_rejected" in result.warnings
    assert result.candidates[0].score.review == 0.0


class _CrossEncoder:
    def rerank(self, query, documents, batch_size):  # type: ignore[no-untyped-def]
        assert query == "query"
        assert tuple(documents) == ("first", "second")
        assert batch_size == 2
        return (2.0, -2.0)


async def test_fastembed_product_reranker_normalizes_cross_encoder_logits() -> None:
    reranker = FastEmbedProductReranker(
        model=_CrossEncoder(),
        batch_size=2,
        timeout_seconds=1,
    )

    scores = await reranker.score(query="query", documents=("first", "second"))

    assert scores[0] > 0.8
    assert scores[1] < 0.2


class _Retriever:
    async def search(self, **_values):  # type: ignore[no-untyped-def]
        return (ProductRetrievalHit("TPE", dense_score=1.0, fusion_score=1.0),)


async def test_product_index_hits_are_rechecked_against_active_catalog_facts() -> None:
    catalog = InMemoryProductCatalog(
        (product("TPE", material="TPE"), product("SILICONE", material="Silicone"))
    )
    service = ProductRecommendationService(catalog, candidate_retriever=_Retriever())

    result = await service.recommend(
        tenant_id="tenant-a",
        site_id="site-a",
        question="Recommend an option",
        now=NOW,
    )

    assert [item.product.sku for item in result.candidates] == ["TPE"]
