from dataclasses import replace

from app.domain.models import ExperienceGuidance, RetrievalContext, RetrievalPlan, SupportIntent
from app.domain.rules import classify_support_intent

_TRANSACTIONAL_INTENTS = frozenset(
    {
        SupportIntent.PRODUCT_PRICE,
        SupportIntent.PRODUCT_STOCK,
        SupportIntent.DELIVERY_ESTIMATE,
        SupportIntent.SHIPPING_COVERAGE,
        SupportIntent.ORDER_STATUS,
        SupportIntent.ORDER_TRACKING,
        SupportIntent.ORDER_CANCELLATION,
        SupportIntent.REFUND,
        SupportIntent.RETURN_POLICY,
        SupportIntent.LEGAL_SALES,
        SupportIntent.PAYMENT_METHODS,
    }
)


class RetrievalPlanningService:
    def __init__(
        self,
        *,
        enabled: bool = True,
        query_enhancement_enabled: bool = True,
    ) -> None:
        self._enabled = enabled
        self._query_enhancement_enabled = query_enhancement_enabled

    def plan(
        self,
        *,
        question: str,
        context: RetrievalContext,
        base_filters: dict[str, object],
        experience_guidance: tuple[ExperienceGuidance, ...] = (),
    ) -> RetrievalPlan:
        intent = classify_support_intent(question)
        entities = tuple(
            dict.fromkeys(
                value
                for value in (
                    context.pending_product_sku,
                    context.active_product_sku,
                    *context.candidate_product_skus,
                )
                if value
            )
        )
        filters = dict(base_filters)
        filters["strict_language"] = intent in _TRANSACTIONAL_INTENTS
        query_variants = (
            _query_variants(question, intent, entities)
            if self._query_enhancement_enabled and self._enabled
            else ()
        )
        query_variants = _merge_experience_hints(
            query_variants,
            experience_guidance,
        )
        if not self._enabled:
            filters.pop("strict_language", None)
        return RetrievalPlan(
            intent=intent.value,
            entities=entities,
            sources=_sources(intent),
            filters=filters,
            query_variants=query_variants,
            top_k=3 if entities or intent in _TRANSACTIONAL_INTENTS else 5,
            rerank_mode="deterministic",
            latency_budget_ms=500 if intent in _TRANSACTIONAL_INTENTS else 800,
            fallback_policy=(
                "strict_evidence" if intent in _TRANSACTIONAL_INTENTS else "hybrid_degraded"
            ),
        )

    def with_filters(self, plan: RetrievalPlan, filters: dict[str, object]) -> RetrievalPlan:
        return replace(plan, filters=filters)


def _sources(intent: SupportIntent) -> tuple[str, ...]:
    if intent in {
        SupportIntent.ORDER_STATUS,
        SupportIntent.ORDER_TRACKING,
        SupportIntent.ORDER_CANCELLATION,
        SupportIntent.REFUND,
        SupportIntent.PAYMENT_METHODS,
    }:
        return ("realtime",)
    if intent in {
        SupportIntent.PRODUCT_PRICE,
        SupportIntent.PRODUCT_STOCK,
        SupportIntent.DELIVERY_ESTIMATE,
        SupportIntent.SHIPPING_COVERAGE,
    }:
        return ("realtime", "knowledge")
    return ("knowledge",)


def _query_variants(
    question: str,
    intent: SupportIntent,
    entities: tuple[str, ...],
) -> tuple[str, ...]:
    if entities or intent in _TRANSACTIONAL_INTENTS or len(question.split()) < 5:
        return ()
    normalized = " ".join(question.split())
    if intent is SupportIntent.PRODUCT_RECOMMENDATION:
        return (
            f"{normalized} product attributes material size weight price",
            f"{normalized} suitable options comparison",
        )
    if intent is SupportIntent.PRODUCT_CARE:
        return (f"{normalized} approved care cleaning storage material",)
    if intent is SupportIntent.GENERAL:
        return (f"{normalized} customer support FAQ",)
    return ()


def _merge_experience_hints(
    query_variants: tuple[str, ...],
    guidance: tuple[ExperienceGuidance, ...],
) -> tuple[str, ...]:
    hints = tuple(
        dict.fromkeys(
            hint.strip()[:300] for item in guidance for hint in item.retrieval_hints if hint.strip()
        )
    )
    return tuple(dict.fromkeys((*query_variants, *hints)))[:4]
