from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.domain.models import ConversationWorkingMemory, ProductDataStatus, ProductSnapshot
from app.domain.models.recommendation import (
    CandidateReviewInput,
    ProductRecommendationCandidate,
    ProductRecommendationConstraint,
    ProductRecommendationMode,
    ProductRecommendationPlan,
    ProductRecommendationResult,
    ProductRecommendationScore,
    RecommendationConstraintStrength,
    RecommendationPlannerAdvice,
)
from app.domain.ports import EmbeddingProviderPort, ProductCatalogPort
from app.domain.ports.product_recommendation import (
    CandidateReviewPort,
    ProductCandidateRetrieverPort,
    ProductRerankerPort,
    ProductRetrievalHit,
    RecommendationPlannerPort,
)
from app.domain.rules.recommendation import (
    bm25_scores,
    build_product_recommendation_plan,
    cosine_similarity,
    decimal_value,
    hard_constraint_labels,
    product_business_score,
    product_passes_hard_constraints,
    product_preference_score,
    product_search_text,
    reciprocal_rank_fusion,
    replacement_price_similarity,
    structured_product_similarity,
)


@dataclass(slots=True)
class _WorkingCandidate:
    product: ProductSnapshot
    structured: float
    coverage: float
    preference: float
    sparse: float
    fusion: float = 0.0
    dense: float = 0.0
    rerank: float = 0.0
    price_fit: float = 0.0
    business: float = 0.0
    review: float = 0.0
    review_reason: str | None = None
    review_rejected: bool = False
    final: float = 0.0
    matched_fields: tuple[str, ...] = ()
    preference_reasons: tuple[str, ...] = ()
    missing_facts: tuple[str, ...] = ()


class ProductRecommendationService:
    """Tenant-scoped product selection; response drafting remains outside this service."""

    def __init__(
        self,
        catalog: ProductCatalogPort,
        *,
        embedding_provider: EmbeddingProviderPort | None = None,
        reranker: ProductRerankerPort | None = None,
        planner: RecommendationPlannerPort | None = None,
        reviewer: CandidateReviewPort | None = None,
        candidate_retriever: ProductCandidateRetrieverPort | None = None,
        catalog_limit: int = 10_000,
        dense_pool_size: int = 60,
        rerank_pool_size: int = 36,
        minimum_score: float = 0.15,
        specification_max_age_days: int = 30,
        price_max_age_days: int = 7,
        stock_max_age_days: int = 7,
    ) -> None:
        self._catalog = catalog
        self._embedding_provider = embedding_provider
        self._reranker = reranker
        self._planner = planner
        self._reviewer = reviewer
        self._candidate_retriever = candidate_retriever
        self._catalog_limit = max(1, catalog_limit)
        self._dense_pool_size = max(3, dense_pool_size)
        self._rerank_pool_size = max(3, rerank_pool_size)
        self._minimum_score = max(0.0, min(1.0, minimum_score))
        self._specification_max_age = timedelta(days=specification_max_age_days)
        self._price_max_age = timedelta(days=price_max_age_days)
        self._stock_max_age = timedelta(days=stock_max_age_days)

    async def recommend(
        self,
        *,
        tenant_id: str,
        site_id: str | None,
        question: str,
        sales_memory: ConversationWorkingMemory | None = None,
        source_product: ProductSnapshot | None = None,
        limit: int = 3,
        now: datetime | None = None,
    ) -> ProductRecommendationResult:
        evaluated_at = (now or datetime.now(UTC)).astimezone(UTC)
        memory = sales_memory or ConversationWorkingMemory()
        plan = build_product_recommendation_plan(
            question,
            memory_facts=memory.preference_facts,
            destination=memory.country_code,
            source_product=source_product,
        )
        warnings: list[str] = []
        plan = await self._apply_planner(question, plan, warnings)
        query_text = self._query_text(question, source_product, plan)
        products, retrieval_hits = await self._load_candidate_products(
            tenant_id=tenant_id,
            site_id=site_id,
            query_text=query_text,
            mode=plan.mode,
            warnings=warnings,
        )
        eligible: list[ProductSnapshot] = []
        missing_hard_facts: set[str] = set()
        for product in products:
            if source_product is not None and product.product_key == source_product.product_key:
                continue
            fresh = self._fresh_product(product, evaluated_at)
            if fresh is None:
                continue
            accepted, _violations, missing = product_passes_hard_constraints(
                fresh,
                plan,
                source_product=source_product,
            )
            if not accepted:
                missing_hard_facts.update(missing)
                continue
            eligible.append(fresh)
        if not eligible:
            reason = (
                "strict_constraints_require_missing_product_facts"
                if missing_hard_facts
                else "no_product_satisfies_all_hard_constraints"
            )
            return ProductRecommendationResult(
                mode=plan.mode,
                hard_constraint_labels=hard_constraint_labels(plan),
                warnings=tuple(
                    (*warnings,)
                    + tuple(
                        f"missing_required_fact:{field}" for field in sorted(missing_hard_facts)
                    )
                ),
                no_match_reason=reason,
            )

        documents = [product_search_text(product) for product in eligible]
        sparse_scores = bm25_scores(query_text, documents)
        working: list[_WorkingCandidate] = []
        for product, sparse in zip(eligible, sparse_scores, strict=True):
            retrieval_hit = retrieval_hits.get(product.product_key)
            structured, coverage, matched, structured_missing = structured_product_similarity(
                source_product,
                product,
            )
            preference, preference_reasons, preference_missing = product_preference_score(
                product,
                plan,
            )
            working.append(
                _WorkingCandidate(
                    product=product,
                    structured=structured,
                    coverage=coverage,
                    preference=preference,
                    sparse=max(sparse, retrieval_hit.sparse_score if retrieval_hit else 0.0),
                    dense=retrieval_hit.dense_score if retrieval_hit else 0.0,
                    matched_fields=matched,
                    preference_reasons=preference_reasons,
                    missing_facts=tuple(dict.fromkeys((*structured_missing, *preference_missing))),
                )
            )

        initial_fusion = reciprocal_rank_fusion(
            (
                [item.sparse for item in working],
                [item.structured for item in working],
                [item.preference for item in working],
            )
        )
        for item, fusion in zip(working, initial_fusion, strict=True):
            item.fusion = fusion
        working.sort(
            key=lambda item: (
                item.fusion,
                item.coverage,
                -(decimal_value(item.product.price) or Decimal("999999999")),
                item.product.product_key,
            ),
            reverse=True,
        )
        working = working[: self._dense_pool_size]

        if not retrieval_hits:
            await self._apply_dense_scores(query_text, working, warnings)
        fused = reciprocal_rank_fusion(
            (
                [item.sparse for item in working],
                [item.structured for item in working],
                [item.preference for item in working],
                [item.dense for item in working],
            )
        )

        for item, fusion in zip(working, fused, strict=True):
            item.fusion = fusion
        working.sort(key=lambda item: item.fusion, reverse=True)
        working = working[: self._rerank_pool_size]
        await self._apply_rerank_scores(query_text, working, warnings)
        self._apply_final_scores(working, plan.mode, source_product, bool(plan.soft_constraints))
        await self._apply_candidate_review(query_text, working, warnings)

        thresholded = (
            working
            if plan.mode is ProductRecommendationMode.EXACT_MATCH
            else [item for item in working if item.final >= self._minimum_score]
        )
        reviewed = [item for item in thresholded if not item.review_rejected]
        if reviewed:
            thresholded = reviewed
        elif thresholded:
            warnings.append("reviewer_cannot_remove_all_candidates")
        thresholded.sort(key=self._rank_key)
        selected = self._diversified(thresholded, max(1, limit))
        candidates = tuple(
            self._to_candidate(item, plan.mode, plan.hard_constraints) for item in selected
        )
        return ProductRecommendationResult(
            mode=plan.mode,
            candidates=candidates,
            hard_constraint_labels=hard_constraint_labels(plan),
            warnings=tuple(warnings),
            no_match_reason=None if candidates else "no_product_reached_similarity_threshold",
        )

    async def _load_candidate_products(
        self,
        *,
        tenant_id: str,
        site_id: str | None,
        query_text: str,
        mode: ProductRecommendationMode,
        warnings: list[str],
    ) -> tuple[tuple[ProductSnapshot, ...], dict[str, ProductRetrievalHit]]:
        if (
            self._candidate_retriever is not None
            and mode is not ProductRecommendationMode.EXACT_MATCH
        ):
            try:
                hits = tuple(
                    await self._candidate_retriever.search(
                        tenant_id=tenant_id,
                        site_id=site_id,
                        query=query_text,
                        limit=min(1000, self._catalog_limit),
                    )
                )
                if hits:
                    products = await self._catalog.list_active_products_by_keys(
                        tenant_id=tenant_id,
                        site_id=site_id,
                        product_keys=tuple(hit.product_key for hit in hits),
                    )
                    if products:
                        return products, {hit.product_key: hit for hit in hits}
                warnings.append("product_recommendation_index_empty")
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"product_recommendation_index_failed:{type(exc).__name__}")
        products = await self._catalog.list_active_products(
            tenant_id=tenant_id,
            site_id=site_id,
            limit=self._catalog_limit,
        )
        return products, {}

    async def _apply_planner(
        self,
        question: str,
        plan: ProductRecommendationPlan,
        warnings: list[str],
    ) -> ProductRecommendationPlan:
        if self._planner is None:
            return plan
        try:
            advice = await self._planner.plan(query=question, mode=plan.mode)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"recommendation_planner_failed:{type(exc).__name__}")
            return plan
        return self._merge_planner_advice(plan, advice, warnings)

    @staticmethod
    def _merge_planner_advice(
        plan: ProductRecommendationPlan,
        advice: RecommendationPlannerAdvice,
        warnings: list[str],
    ) -> ProductRecommendationPlan:
        allowed_fields = {"material", "lightweight", "compact", "preferred_term"}
        additions: list[ProductRecommendationConstraint] = []
        existing = {(item.field, item.value.casefold()) for item in plan.constraints}
        for preference in advice.soft_preferences[:4]:
            field = preference.field.strip().casefold()
            value = preference.value.strip()
            if field not in allowed_fields or not value:
                warnings.append("recommendation_planner_preference_rejected")
                continue
            key = (field, value.casefold())
            if key in existing:
                continue
            additions.append(
                ProductRecommendationConstraint(
                    field=field,
                    operator="contains" if field in {"material", "preferred_term"} else "prefer",
                    value=value,
                    strength=RecommendationConstraintStrength.SOFT,
                    source="planner",
                    weight=max(0.05, min(1.0, preference.weight)),
                )
            )
            existing.add(key)
        if advice.request_second_soft_retrieval:
            warnings.append("recommendation_planner_requested_soft_expansion")
        return replace(plan, constraints=(*plan.constraints, *additions))

    async def _apply_candidate_review(
        self,
        query_text: str,
        candidates: list[_WorkingCandidate],
        warnings: list[str],
    ) -> None:
        if self._reviewer is None or not candidates:
            return
        review_pool = sorted(candidates, key=self._rank_key)[:8]
        inputs = tuple(self._review_input(item) for item in review_pool)
        try:
            reviews = await self._reviewer.review(query=query_text, candidates=inputs)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"candidate_review_failed:{type(exc).__name__}")
            return
        candidates_by_key = {item.product.product_key: item for item in review_pool}
        input_fields = {item.candidate_key: set(item.facts) for item in inputs}
        for review in reviews:
            item = candidates_by_key.get(review.candidate_key)
            if item is None:
                warnings.append("candidate_review_unknown_candidate")
                continue
            if not set(review.evidence_field_ids).issubset(input_fields[review.candidate_key]):
                warnings.append("candidate_review_evidence_rejected")
                continue
            score = max(0.0, min(1.0, float(review.score)))
            item.review = score
            item.review_reason = review.reason[:240]
            item.final = 0.80 * item.final + 0.20 * score
            item.review_rejected = not review.relevant and score < 0.20

    @staticmethod
    def _review_input(item: _WorkingCandidate) -> CandidateReviewInput:
        product = item.product
        facts = {
            key: value
            for key, value in {
                "name": product.name,
                "sku": product.sku or product.mpn or "",
                "brand": product.brand or "",
                "material": product.material or "",
                "dimensions": "; ".join(
                    f"{key}: {value}" for key, value in sorted(product.dimensions.items())
                ),
                "weight": product.weight or "",
                "price": product.price or "",
                "currency": product.currency or "",
                "stock": product.stock_status or "",
                "shipping_regions": ", ".join(product.shipping_regions),
                "shipping_warehouse": product.shipping_warehouse or "",
            }.items()
            if value
        }
        return CandidateReviewInput(candidate_key=product.product_key, facts=facts)

    def _fresh_product(
        self,
        product: ProductSnapshot,
        evaluated_at: datetime,
    ) -> ProductSnapshot | None:
        fetched_at = product.fetched_at.astimezone(UTC)
        age = evaluated_at - fetched_at
        if product.status is not ProductDataStatus.VALID or age > self._specification_max_age:
            return None
        return replace(
            product,
            price=product.price if age <= self._price_max_age else None,
            currency=product.currency if age <= self._price_max_age else None,
            stock_status=product.stock_status if age <= self._stock_max_age else None,
        )

    async def _apply_dense_scores(
        self,
        query_text: str,
        candidates: list[_WorkingCandidate],
        warnings: list[str],
    ) -> None:
        if self._embedding_provider is None or not candidates:
            warnings.append("dense_retrieval_unavailable")
            return
        try:
            documents = [product_search_text(item.product) for item in candidates]
            embeddings = await self._embedding_provider.embed([query_text, *documents])
            if len(embeddings) != len(documents) + 1:
                raise ValueError("embedding provider returned an unexpected vector count")
            query_embedding = embeddings[0]
            for item, embedding in zip(candidates, embeddings[1:], strict=True):
                item.dense = cosine_similarity(query_embedding, embedding)
        except Exception as exc:
            warnings.append(f"dense_retrieval_failed:{type(exc).__name__}")

    async def _apply_rerank_scores(
        self,
        query_text: str,
        candidates: list[_WorkingCandidate],
        warnings: list[str],
    ) -> None:
        if self._reranker is None:
            for item in candidates:
                item.rerank = 0.70 * item.fusion + 0.30 * item.sparse
            warnings.append("cross_encoder_unavailable")
            return
        try:
            scores = list(
                await self._reranker.score(
                    query=query_text,
                    documents=[product_search_text(item.product) for item in candidates],
                )
            )
            if len(scores) != len(candidates):
                raise ValueError("reranker returned an unexpected score count")
            for item, score in zip(candidates, scores, strict=True):
                item.rerank = max(0.0, min(1.0, float(score)))
        except Exception as exc:
            warnings.append(f"cross_encoder_failed:{type(exc).__name__}")
            for item in candidates:
                item.rerank = 0.70 * item.fusion + 0.30 * item.sparse

    @staticmethod
    def _apply_final_scores(
        candidates: list[_WorkingCandidate],
        mode: ProductRecommendationMode,
        source: ProductSnapshot | None,
        has_preferences: bool,
    ) -> None:
        for item in candidates:
            item.business = product_business_score(item.product)
            item.price_fit = (
                replacement_price_similarity(source, item.product) if source is not None else 0.0
            )
            semantic = 0.60 * item.dense + 0.40 * item.sparse
            if mode is ProductRecommendationMode.SUBSTITUTE:
                base_similarity = (
                    0.40 * item.rerank
                    + 0.40 * item.structured
                    + 0.10 * item.sparse
                    + 0.10 * item.dense
                )
                similarity = (
                    0.65 * base_similarity + 0.35 * item.preference
                    if has_preferences
                    else base_similarity
                )
                item.final = 0.80 * similarity + 0.15 * item.price_fit + 0.05 * item.business
            else:
                structured_preference = max(item.structured, item.preference)
                item.final = (
                    0.40 * item.rerank
                    + 0.30 * structured_preference
                    + 0.15 * semantic
                    + 0.10 * item.coverage
                    + 0.05 * item.business
                )

    @staticmethod
    def _to_candidate(
        item: _WorkingCandidate,
        mode: ProductRecommendationMode,
        hard_constraints: tuple,
    ) -> ProductRecommendationCandidate:
        reasons = list(item.preference_reasons)
        fields = set(item.matched_fields)
        preference_fields = {
            "material_match": "material",
            "lightweight_option": "weight",
            "compact_option": "dimensions",
        }
        fields.update(
            evidence_field
            for reason in item.preference_reasons
            if (evidence_field := preference_fields.get(reason)) is not None
        )
        if mode is ProductRecommendationMode.SUBSTITUTE:
            reasons.extend(f"source_{field}_match" for field in item.matched_fields)
        for constraint in hard_constraints:
            reason = {
                "max_price": "within_budget",
                "currency": "currency_match",
                "max_weight_kg": "within_weight_limit",
                "min_height_cm": "within_height_range",
                "max_height_cm": "within_height_range",
                "material": "material_match",
                "excluded_material": "excluded_material_avoided",
                "cheaper_than_source": "cheaper_than_source",
                "lighter_than_source": "lighter_than_source",
            }.get(constraint.field)
            if reason:
                reasons.append(reason)
                fields.add(
                    {
                        "max_price": "price",
                        "max_weight_kg": "weight",
                        "min_height_cm": "dimensions",
                        "max_height_cm": "dimensions",
                        "excluded_material": "material",
                        "cheaper_than_source": "price",
                        "lighter_than_source": "weight",
                    }.get(constraint.field, constraint.field)
                )
        if not reasons:
            reasons.append("best_evidence_backed_match")
        missing = tuple(dict.fromkeys(item.missing_facts))
        tradeoffs = tuple(f"{field}_unknown" for field in missing)
        return ProductRecommendationCandidate(
            product=item.product,
            score=ProductRecommendationScore(
                dense=round(item.dense, 4),
                sparse=round(item.sparse, 4),
                structured=round(item.structured, 4),
                rerank=round(item.rerank, 4),
                preference=round(item.preference, 4),
                price_fit=round(item.price_fit, 4),
                business=round(item.business, 4),
                evidence_coverage=round(item.coverage, 4),
                review=round(item.review, 4),
                final=round(item.final, 4),
            ),
            match_reasons=tuple(dict.fromkeys(reasons)),
            tradeoffs=tradeoffs,
            missing_facts=missing,
            evidence_fields=tuple(sorted(fields)),
        )

    @staticmethod
    def _rank_key(item: _WorkingCandidate) -> tuple[float, float, Decimal, str]:
        price = decimal_value(item.product.price) or Decimal("999999999")
        return (-item.final, -item.coverage, price, item.product.product_key)

    @staticmethod
    def _diversified(
        candidates: list[_WorkingCandidate],
        limit: int,
    ) -> list[_WorkingCandidate]:
        selected: list[_WorkingCandidate] = []
        seen_urls: set[str] = set()
        for item in candidates:
            url = item.product.canonical_url.casefold()
            if url in seen_urls:
                continue
            selected.append(item)
            seen_urls.add(url)
            if len(selected) >= limit:
                break
        return selected

    @staticmethod
    def _query_text(
        question: str,
        source: ProductSnapshot | None,
        plan: ProductRecommendationPlan,
    ) -> str:
        planned_preferences = " ".join(
            constraint.value
            for constraint in plan.soft_constraints
            if constraint.source == "planner"
        )
        if source is None:
            return " ".join(value for value in (question.strip(), planned_preferences) if value)
        return " ".join(
            value
            for value in (
                product_search_text(source),
                "Customer preference:",
                question.strip(),
                planned_preferences,
            )
            if value
        )
