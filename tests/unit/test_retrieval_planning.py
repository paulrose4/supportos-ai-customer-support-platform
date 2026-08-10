from app.application.services import RetrievalPlanningService
from app.domain.models import RetrievalContext, SupportIntent


def test_retrieval_plan_is_strict_for_transactional_delivery_queries() -> None:
    plan = RetrievalPlanningService().plan(
        question="How many days will SKU-100 take to arrive?",
        context=RetrievalContext(active_product_sku="SKU-100", country_code="US"),
        base_filters={"site_id": "site-a"},
    )

    assert plan.intent == SupportIntent.DELIVERY_ESTIMATE.value
    assert plan.filters["strict_language"] is True
    assert plan.query_variants == ()
    assert plan.sources == ("realtime", "knowledge")


def test_retrieval_plan_uses_bounded_variants_only_for_ambiguous_non_transactional_queries() -> (
    None
):
    plan = RetrievalPlanningService().plan(
        question="I am looking for a compact option for easy handling",
        context=RetrievalContext(),
        base_filters={"site_id": "site-a"},
    )

    assert plan.intent == SupportIntent.PRODUCT_RECOMMENDATION.value
    assert len(plan.query_variants) == 2
    assert plan.filters["strict_language"] is False
    assert plan.top_k == 5


def test_disabled_retrieval_plan_does_not_add_filters_or_query_variants() -> None:
    plan = RetrievalPlanningService(enabled=False).plan(
        question="I am looking for a compact option for easy handling",
        context=RetrievalContext(),
        base_filters={"site_id": "site-a"},
    )

    assert plan.filters == {"site_id": "site-a"}
    assert plan.query_variants == ()
