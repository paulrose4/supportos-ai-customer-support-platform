import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from urllib.parse import urlsplit, urlunsplit

from app.application.services.product_recommendation import ProductRecommendationService
from app.domain.models import (
    ConversationWorkingMemory,
    KnowledgeEvidence,
    ProductDataStatus,
    ProductRecommendationResult,
    ProductSnapshot,
    SupportIntent,
    TurnPlan,
)
from app.domain.ports import ProductCatalogPort, ProductLookup
from app.domain.rules import (
    classify_support_intent,
    is_relative_product_request,
    product_reference_identifiers,
    product_reference_urls,
)

_PRODUCT_FACT_INTENTS = frozenset(
    {
        SupportIntent.PRODUCT_PRICE,
        SupportIntent.PRODUCT_STOCK,
        SupportIntent.PRODUCT_MATERIAL,
        SupportIntent.PRODUCT_DIMENSIONS,
        SupportIntent.PRODUCT_WEIGHT,
        SupportIntent.SHIPPING_COVERAGE,
    }
)


@dataclass(frozen=True, slots=True)
class ProductFactAnswerResult:
    status: str
    message: str | None = None
    product: ProductSnapshot | None = None
    evidence: tuple[KnowledgeEvidence, ...] = ()
    citations: tuple[str, ...] = ()
    products: tuple[ProductSnapshot, ...] = ()


class ProductCatalogAnswerService:
    def __init__(
        self,
        catalog: ProductCatalogPort,
        *,
        specification_max_age_days: int = 30,
        price_max_age_days: int = 7,
        stock_max_age_days: int = 7,
        recommendation_service: ProductRecommendationService | None = None,
    ) -> None:
        self._catalog = catalog
        self._specification_max_age = timedelta(days=specification_max_age_days)
        self._price_max_age = timedelta(days=price_max_age_days)
        self._stock_max_age = timedelta(days=stock_max_age_days)
        self._recommendations = recommendation_service or ProductRecommendationService(
            catalog,
            specification_max_age_days=specification_max_age_days,
            price_max_age_days=price_max_age_days,
            stock_max_age_days=stock_max_age_days,
        )

    async def execute(
        self,
        *,
        tenant_id: str,
        site_id: str | None,
        question: str,
        page_path: str,
        language: str,
        sales_memory: ConversationWorkingMemory | None = None,
        turn_plan: TurnPlan | None = None,
        now: datetime | None = None,
    ) -> ProductFactAnswerResult:
        intent = classify_support_intent(question)
        parsed_page_path = urlsplit(page_path).path
        normalized_page_path = parsed_page_path or "/"
        if intent is SupportIntent.DISCOUNT:
            return ProductFactAnswerResult(
                status="general_guidance",
                message=_discount_guidance(language),
            )

        urls = product_reference_urls(question)
        identifiers = product_reference_identifiers(question)
        explicit_reference = bool(urls or identifiers)
        evaluated_at = (now or datetime.now(UTC)).astimezone(UTC)
        if is_relative_product_request(question) and (
            explicit_reference or normalized_page_path != "/"
        ):
            product = await self._find_product(
                tenant_id=tenant_id,
                site_id=site_id,
                urls=urls,
                identifiers=identifiers,
                page_path=None if explicit_reference else normalized_page_path,
            )
            if product is None:
                return ProductFactAnswerResult(
                    status="product_not_found" if explicit_reference else "insufficient"
                )
            recommendation = await self._recommendations.recommend(
                tenant_id=tenant_id,
                site_id=site_id,
                question=question,
                sales_memory=sales_memory,
                source_product=product,
                now=evaluated_at,
            )
            if not recommendation.candidates:
                return ProductFactAnswerResult(status="insufficient")
            return _recommendation_context(recommendation)
        if intent is SupportIntent.PRODUCT_COMPARISON:
            products = await self._find_referenced_products(
                tenant_id=tenant_id,
                site_id=site_id,
                urls=urls,
                identifiers=identifiers,
            )
            if len(products) < 2:
                return ProductFactAnswerResult(status="product_not_found")
            products = _usable_catalog_products(
                products,
                question=question,
                sales_memory=sales_memory,
                evaluated_at=evaluated_at,
                specification_max_age=self._specification_max_age,
                price_max_age=self._price_max_age,
                stock_max_age=self._stock_max_age,
            )
            if len(products) < 2:
                return ProductFactAnswerResult(status="insufficient")
            return _catalog_context(products, question=question, sales_memory=sales_memory)
        if intent is SupportIntent.PRODUCT_RECOMMENDATION:
            recommendation = await self._recommendations.recommend(
                tenant_id=tenant_id,
                site_id=site_id,
                question=question,
                sales_memory=sales_memory,
                now=evaluated_at,
            )
            if not recommendation.candidates:
                return ProductFactAnswerResult(status="insufficient")
            return _recommendation_context(recommendation)
        planner_product_request = _planner_requests_product_snapshot(turn_plan)
        current_path = (
            normalized_page_path
            if normalized_page_path != "/"
            and (
                intent
                in {
                    *_PRODUCT_FACT_INTENTS,
                    SupportIntent.PRODUCT_CARE,
                    SupportIntent.DELIVERY_ESTIMATE,
                }
                or (intent is SupportIntent.GENERAL and _refers_to_current_product(question))
                or planner_product_request
            )
            else None
        )
        needs_product = (
            explicit_reference
            or intent in _PRODUCT_FACT_INTENTS
            or current_path is not None
            or planner_product_request
        )
        if not needs_product and current_path is None:
            return ProductFactAnswerResult(status="not_applicable")

        product = await self._find_product(
            tenant_id=tenant_id,
            site_id=site_id,
            urls=urls,
            identifiers=identifiers,
            page_path=None if explicit_reference else current_path,
        )
        if product is None:
            if explicit_reference or intent in _PRODUCT_FACT_INTENTS or planner_product_request:
                return ProductFactAnswerResult(status="product_not_found")
            return ProductFactAnswerResult(status="not_applicable")
        if planner_product_request:
            evidence = _snapshot_evidence(
                product,
                evaluated_at=evaluated_at,
                specification_max_age=self._specification_max_age,
                price_max_age=self._price_max_age,
                stock_max_age=self._stock_max_age,
            )
            approved_fields = tuple(
                item
                for item in evidence.metadata.get("field_facts", [])
                if isinstance(item, dict) and item.get("approved") is True
            )
            if not approved_fields:
                return ProductFactAnswerResult(
                    status="stale",
                    message=_stale_message(language, product.canonical_url),
                    product=product,
                    evidence=(evidence,),
                )
            return ProductFactAnswerResult(
                status="planned_context",
                message=_planned_fact_seed(product, approved_fields, language),
                product=product,
                evidence=(evidence,),
                citations=(f"{evidence.source}#{evidence.chunk_id}",),
            )
        if intent not in _PRODUCT_FACT_INTENTS:
            return ProductFactAnswerResult(status="bound", product=product)

        max_age = self._max_age(intent)
        fetched_at = product.fetched_at.astimezone(UTC)
        if product.status is not ProductDataStatus.VALID or evaluated_at - fetched_at > max_age:
            return ProductFactAnswerResult(
                status="stale",
                message=_stale_message(language, product.canonical_url),
                product=product,
            )

        message = _render_fact_answer(intent, product, language, question)
        if message is None:
            return ProductFactAnswerResult(status="insufficient", product=product)
        evidence = _snapshot_evidence(product)
        citation = f"{evidence.source}#{evidence.chunk_id}"
        return ProductFactAnswerResult(
            status="sufficient",
            message=message,
            product=product,
            evidence=(evidence,),
            citations=(citation,),
        )

    async def _find_product(
        self,
        *,
        tenant_id: str,
        site_id: str | None,
        urls: tuple[str, ...],
        identifiers: tuple[str, ...],
        page_path: str | None,
    ) -> ProductSnapshot | None:
        for url in urls[:3]:
            parsed = urlsplit(url)
            canonical_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
            for lookup in (
                ProductLookup(
                    tenant_id=tenant_id,
                    site_id=site_id,
                    canonical_url=url,
                ),
                ProductLookup(
                    tenant_id=tenant_id,
                    site_id=site_id,
                    canonical_url=canonical_url,
                ),
                ProductLookup(
                    tenant_id=tenant_id,
                    site_id=site_id,
                    page_path=parsed.path or "/",
                ),
            ):
                product = await self._catalog.find_exact(lookup)
                if product is not None:
                    return product
        for identifier in identifiers[:6]:
            product = await self._catalog.find_exact(
                ProductLookup(
                    tenant_id=tenant_id,
                    site_id=site_id,
                    sku=identifier,
                    mpn=identifier,
                )
            )
            if product is not None:
                return product
        if page_path:
            return await self._catalog.find_exact(
                ProductLookup(
                    tenant_id=tenant_id,
                    site_id=site_id,
                    page_path=page_path,
                )
            )
        return None

    async def _find_referenced_products(
        self,
        *,
        tenant_id: str,
        site_id: str | None,
        urls: tuple[str, ...],
        identifiers: tuple[str, ...],
    ) -> tuple[ProductSnapshot, ...]:
        products: dict[str, ProductSnapshot] = {}
        for url in urls[:5]:
            product = await self._catalog.find_exact(
                ProductLookup(
                    tenant_id=tenant_id,
                    site_id=site_id,
                    canonical_url=url,
                )
            )
            if product is not None:
                products[product.product_key] = product
        for identifier in identifiers[:12]:
            product = await self._catalog.find_exact(
                ProductLookup(
                    tenant_id=tenant_id,
                    site_id=site_id,
                    sku=identifier,
                    mpn=identifier,
                )
            )
            if product is not None:
                products[product.product_key] = product
        return tuple(products.values())

    def _max_age(self, intent: SupportIntent) -> timedelta:
        if intent is SupportIntent.PRODUCT_PRICE:
            return self._price_max_age
        if intent is SupportIntent.PRODUCT_STOCK:
            return self._stock_max_age
        return self._specification_max_age


def _snapshot_evidence(
    product: ProductSnapshot,
    *,
    recommendation: dict[str, object] | None = None,
    evaluated_at: datetime | None = None,
    specification_max_age: timedelta = timedelta(days=30),
    price_max_age: timedelta = timedelta(days=7),
    stock_max_age: timedelta = timedelta(days=7),
) -> KnowledgeEvidence:
    facts = {
        "sku": product.sku,
        "mpn": product.mpn,
        "name": product.name,
        "brand": product.brand,
        "material": product.material,
        "attributes": product.attributes,
        "dimensions": product.dimensions,
        "weight": product.weight,
        "price": product.price,
        "currency": product.currency,
        "stock_status": _normalized_stock_status(product.stock_status),
        "shipping_warehouse": product.shipping_warehouse,
        "shipping_regions": product.shipping_regions,
    }
    field_facts = _field_fact_payloads(
        product,
        evaluated_at=evaluated_at,
        specification_max_age=specification_max_age,
        price_max_age=price_max_age,
        stock_max_age=stock_max_age,
    )
    approved_text = "\n".join(
        f"{item['predicate']}: {item['value']}" for item in field_facts if item["approved"] is True
    )
    return KnowledgeEvidence(
        chunk_id=f"product-snapshot-{product.snapshot_id}-{product.product_key}",
        document_id=f"product:{product.product_key}",
        text=approved_text,
        score=1.0,
        source=product.canonical_url,
        metadata={
            "tenant_id": product.tenant_id,
            "site_id": product.site_id,
            "source_type": "postgres_product_snapshot",
            "snapshot_id": product.snapshot_id,
            "fetched_at": product.fetched_at.isoformat(),
            "category": "product",
            "product": facts,
            "field_facts": field_facts,
            **({"recommendation": recommendation} if recommendation is not None else {}),
        },
    )


def _field_fact_payloads(
    product: ProductSnapshot,
    *,
    evaluated_at: datetime | None,
    specification_max_age: timedelta,
    price_max_age: timedelta,
    stock_max_age: timedelta,
) -> list[dict[str, object]]:
    rows: list[tuple[str, object, timedelta]] = [
        ("sku", product.sku, specification_max_age),
        ("mpn", product.mpn, specification_max_age),
        ("name", product.name, specification_max_age),
        ("brand", product.brand, specification_max_age),
        ("material", product.material, specification_max_age),
        ("weight", product.weight, specification_max_age),
        ("price", _price_fact_value(product), price_max_age),
        ("stock_status", _normalized_stock_status(product.stock_status), stock_max_age),
        ("snapshot_checked_at", product.fetched_at.date().isoformat(), specification_max_age),
        ("shipping_warehouse", product.shipping_warehouse, specification_max_age),
        ("shipping_regions", ", ".join(product.shipping_regions), specification_max_age),
    ]
    rows.extend(
        (f"dimension:{key}", value, specification_max_age)
        for key, value in product.dimensions.items()
    )
    rows.extend(
        (f"attribute:{key}", value, specification_max_age)
        for key, value in product.attributes.items()
        if key not in product.dimensions
    )
    checked_at = (evaluated_at or product.fetched_at).astimezone(UTC)
    fetched_at = product.fetched_at.astimezone(UTC)
    payload: list[dict[str, object]] = []
    for predicate, raw_value, max_age in rows:
        value = str(raw_value or "").strip()
        if not value:
            continue
        approved = product.status is ProductDataStatus.VALID and checked_at - fetched_at <= max_age
        suffix = sha256(predicate.encode("utf-8")).hexdigest()[:10]
        fact_id = f"product:{product.product_key}:{suffix}"
        payload.append(
            {
                "fact_id": fact_id,
                "subject": f"product:{product.product_key}",
                "predicate": predicate,
                "value": value[:1000],
                "source": product.canonical_url,
                "citation": f"{product.canonical_url}#{fact_id}",
                "fetched_at": product.fetched_at.isoformat(),
                "freshness": "current" if approved else "stale",
                "scope": "product_snapshot",
                "approved": approved,
            }
        )
        if len(payload) >= 150:
            break
    return payload


def _price_fact_value(product: ProductSnapshot) -> str | None:
    if not product.price:
        return None
    return " ".join(value for value in (product.price, product.currency) if value)


def _normalized_stock_status(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    if "/" in normalized:
        normalized = normalized.rstrip("/").rsplit("/", 1)[-1]
    return normalized or None


def _planned_fact_seed(
    product: ProductSnapshot,
    facts: tuple[dict[str, object], ...],
    language: str,
) -> str:
    values = "; ".join(f"{item['predicate']}: {item['value']}" for item in facts)
    if language.casefold().startswith("zh"):
        return f"{product.name} 已核实的商品信息：{values}。"
    return f"Verified product details for {product.name}: {values}."


def _planner_requests_product_snapshot(turn_plan: TurnPlan | None) -> bool:
    return bool(
        turn_plan
        and not turn_plan.planner_fallback
        and any(
            need.capability_hint == "product_snapshot_lookup" for need in turn_plan.evidence_needs
        )
    )


def _catalog_context(
    products: tuple[ProductSnapshot, ...],
    *,
    question: str,
    sales_memory: ConversationWorkingMemory | None,
) -> ProductFactAnswerResult:
    evidence = tuple(
        _snapshot_evidence(
            product,
            recommendation=_recommendation_explanation(
                product,
                question=question,
                sales_memory=sales_memory,
            ),
        )
        for product in products[:3]
    )
    products = products[:3]
    return ProductFactAnswerResult(
        status="catalog_context",
        product=products[0],
        products=products,
        evidence=evidence,
        citations=tuple(f"{item.source}#{item.chunk_id}" for item in evidence),
    )


def _recommendation_context(result: ProductRecommendationResult) -> ProductFactAnswerResult:
    products = tuple(item.product for item in result.candidates[:3])
    evidence = tuple(
        _snapshot_evidence(
            item.product,
            recommendation={
                "mode": result.mode.value,
                "match_reasons": list(item.match_reasons),
                "tradeoffs": list(item.tradeoffs),
                "missing_facts": list(item.missing_facts),
                "evidence_fields": list(item.evidence_fields),
                "score_breakdown": {
                    "dense": item.score.dense,
                    "sparse": item.score.sparse,
                    "structured": item.score.structured,
                    "rerank": item.score.rerank,
                    "preference": item.score.preference,
                    "price_fit": item.score.price_fit,
                    "business": item.score.business,
                    "evidence_coverage": item.score.evidence_coverage,
                    "review": item.score.review,
                    "final": item.score.final,
                },
                "hard_constraints": list(result.hard_constraint_labels),
                "warnings": list(result.warnings),
            },
        )
        for item in result.candidates[:3]
    )
    return ProductFactAnswerResult(
        status="catalog_context",
        product=products[0],
        products=products,
        evidence=evidence,
        citations=tuple(f"{item.source}#{item.chunk_id}" for item in evidence),
    )


def _usable_catalog_products(
    products: tuple[ProductSnapshot, ...],
    *,
    question: str,
    sales_memory: ConversationWorkingMemory | None = None,
    evaluated_at: datetime,
    specification_max_age: timedelta,
    price_max_age: timedelta,
    stock_max_age: timedelta,
) -> tuple[ProductSnapshot, ...]:
    preferences = _preference_values(sales_memory)
    price_limit = _price_limit(question) or _decimal(preferences.get("budget_max"))
    preferred_material = preferences.get("material")
    excluded_materials = _preference_set(sales_memory, "excluded_material")
    max_weight = _weight_kg(preferences.get("max_weight"))
    lightweight = (
        any(
            term in question.casefold()
            for term in (
                "lightweight",
                "light weight",
                "easy to handle",
                "lighter",
                "leichter",
                "ligero",
                "ligera",
                "轻便",
                "轻量",
                "轻一点",
                "重量轻",
            )
        )
        or preferences.get("handling") == "lightweight"
    )
    ranked: list[tuple[int, Decimal, ProductSnapshot]] = []
    for product in products:
        fetched_at = product.fetched_at.astimezone(UTC)
        age = evaluated_at - fetched_at
        if product.status is not ProductDataStatus.VALID or age > specification_max_age:
            continue
        price = product.price if age <= price_max_age else None
        stock_status = product.stock_status if age <= stock_max_age else None
        if price_limit is not None:
            numeric_price = _decimal(price)
            if numeric_price is None or numeric_price > price_limit:
                continue
        material = (product.material or "").casefold()
        if excluded_materials and any(value.casefold() in material for value in excluded_materials):
            continue
        if preferred_material and (not material or preferred_material.casefold() not in material):
            continue
        weight_kg = _weight_kg(product.weight)
        if max_weight is not None and (weight_kg is None or weight_kg > max_weight):
            continue
        unknown_penalty = 0
        if lightweight and weight_kg is None:
            unknown_penalty += 4
        if preferences.get("storage") == "compact" and not product.dimensions:
            unknown_penalty += 2
        sort_weight = weight_kg if lightweight and weight_kg is not None else Decimal("999999")
        ranked.append(
            (
                unknown_penalty,
                sort_weight,
                replace(product, price=price, stock_status=stock_status),
            )
        )
    if lightweight:
        ranked.sort(key=lambda item: (item[0], item[1], _decimal(item[2].price) or 0))
    elif price_limit is not None:
        ranked.sort(key=lambda item: (item[0], _decimal(item[2].price) or price_limit))
    else:
        ranked.sort(key=lambda item: (item[0], item[2].name.casefold()))
    return tuple(item[2] for item in ranked)


def _recommendation_explanation(
    product: ProductSnapshot,
    *,
    question: str,
    sales_memory: ConversationWorkingMemory | None,
) -> dict[str, list[str]]:
    preferences = _preference_values(sales_memory)
    reasons: list[str] = []
    tradeoffs: list[str] = []
    missing: list[str] = []
    price_limit = _price_limit(question) or _decimal(preferences.get("budget_max"))
    price = _decimal(product.price)
    if price_limit is not None:
        if price is not None and price <= price_limit:
            reasons.append("within_budget")
        elif price is None:
            missing.append("price")
    preferred_material = preferences.get("material")
    if preferred_material:
        if product.material and preferred_material.casefold() in product.material.casefold():
            reasons.append("material_match")
        elif not product.material:
            missing.append("material")
    lightweight = preferences.get("handling") == "lightweight" or any(
        term in question.casefold()
        for term in ("lightweight", "lighter", "轻一点", "轻量", "leichter")
    )
    if lightweight:
        if product.weight:
            reasons.append("lightweight_option")
        else:
            missing.append("weight")
            tradeoffs.append("weight_unknown")
    if preferences.get("storage") == "compact":
        if product.dimensions:
            reasons.append("dimensions_available_for_storage_check")
        else:
            missing.append("dimensions")
            tradeoffs.append("storage_fit_unverified")
    if not reasons:
        reasons.append("eligible_catalog_candidate")
    return {
        "match_reasons": list(dict.fromkeys(reasons)),
        "tradeoffs": list(dict.fromkeys(tradeoffs)),
        "missing_facts": list(dict.fromkeys(missing)),
    }


def _is_relative_recommendation(question: str) -> bool:
    normalized = question.casefold()
    return any(
        term in normalized
        for term in (
            "cheaper",
            "less expensive",
            "lighter",
            "便宜一点",
            "更便宜",
            "轻一点",
            "更轻",
            "günstiger",
            "leichter",
            "moins cher",
            "plus léger",
            "más barato",
            "más ligero",
        )
    )


def _price_limit(question: str) -> Decimal | None:
    match = re.search(
        r"(?i)(?:under|below|less than|up to|budget(?: is| of| around| about)?|"
        r"低于|不超过|以内|预算(?:大约|大概|约|是|为)?(?:是|为)?|unter)"
        r"\s*[$€£¥￥]?\s*(\d+(?:,\d{3})*(?:\.\d+)?)",
        question,
    )
    return _decimal(match.group(1)) if match else None


def _preference_values(memory: ConversationWorkingMemory | None) -> dict[str, str]:
    if memory is None:
        return {}
    return {
        item.key: item.value for item in memory.preference_facts if item.status.value == "confirmed"
    }


def _preference_set(memory: ConversationWorkingMemory | None, key: str) -> set[str]:
    if memory is None:
        return set()
    return {
        item.value
        for item in memory.preference_facts
        if item.key == key and item.status.value == "confirmed"
    }


def _weight_kg(value: str | None) -> Decimal | None:
    numeric = _decimal(value)
    if numeric is None or not value:
        return None
    normalized = value.casefold()
    if "lb" in normalized or "磅" in normalized:
        return numeric * Decimal("0.45359237")
    return numeric


def _refers_to_current_product(question: str) -> bool:
    normalized = " ".join(question.casefold().split())
    return any(
        term in normalized
        for term in (
            "this product",
            "this item",
            "this model",
            "this product",
            "这个商品",
            "这个产品",
            "这款商品",
            "这款产品",
        )
    )


def _decimal(value: str | None) -> Decimal | None:
    if not value:
        return None
    match = re.search(r"\d+(?:\.\d+)?", value.replace(",", ""))
    if match is None:
        return None
    try:
        return Decimal(match.group(0))
    except InvalidOperation:
        return None


def _render_fact_answer(
    intent: SupportIntent,
    product: ProductSnapshot,
    language: str,
    question: str,
) -> str | None:
    date = product.fetched_at.date().isoformat()
    is_zh = language.casefold().startswith("zh")
    if intent is SupportIntent.PRODUCT_PRICE and product.price:
        value = _price_label(product.price, product.currency)
        if is_zh:
            return (
                f"最近一次核对（{date}）时，这款商品的标价是{value}。价格可能变化，下单前请再确认。"
            )
        return (
            f"When we last checked on {date}, this product was listed at {value}. "
            "Prices can change, so please confirm before ordering."
        )
    if intent is SupportIntent.PRODUCT_STOCK:
        us_stock_listing = _is_us_stock_listing(product)
        stock_label = _stock_label(product.stock_status, is_zh) if product.stock_status else None
        if us_stock_listing:
            if is_zh:
                status = f"，库存状态为“{stock_label}”" if stock_label else ""
                return (
                    f"是的，这款商品在最近一次核对（{date}）时标注为美国仓现货{status}。"
                    "库存会变化，下单前请再确认商品页。"
                )
            status = f" and shown as {stock_label}" if stock_label else ""
            return (
                f"Yes. When we last checked on {date}, this product was listed as US stock"
                f"{status}. Availability can change, so please confirm before ordering."
            )
        if stock_label is None:
            return None
        if is_zh:
            return (
                f"最近一次核对（{date}）时，这款商品的库存状态为“{stock_label}”。"
                "库存会变化，下单前请再确认商品页。"
            )
        return (
            f"When we last checked on {date}, this product was listed as {stock_label}. "
            "Availability can change, so please confirm before ordering."
        )
    if intent is SupportIntent.PRODUCT_MATERIAL and product.material:
        return (
            f"这款商品采用{product.material}材质。"
            if is_zh
            else f"This product is made from {product.material}."
        )
    if intent is SupportIntent.PRODUCT_DIMENSIONS and product.dimensions:
        dimensions = _requested_dimensions(question, product.dimensions)
        if len(dimensions) == 1:
            key, raw_value = dimensions[0]
            value = _readable_measurement(raw_value)
            if _dimension_kind(key) == "height":
                return f"这款商品高{value}。" if is_zh else f"This product is {value} tall."
            if is_zh:
                return f"这款商品的{key}是{value}。"
            return f"The listed {key.casefold()} is {value}."
        values = "；".join(f"{key}：{_readable_measurement(value)}" for key, value in dimensions)
        if not is_zh:
            values = "; ".join(
                f"{key}: {_readable_measurement(value)}" for key, value in dimensions
            )
        return (
            f"这款商品标注的尺寸是：{values}。"
            if is_zh
            else f"Here are the listed dimensions: {values}."
        )
    if intent is SupportIntent.PRODUCT_WEIGHT and product.weight:
        value = _readable_measurement(product.weight)
        return f"这款商品重{value}。" if is_zh else f"This product weighs {value}."
    if intent is SupportIntent.SHIPPING_COVERAGE and product.shipping_regions:
        regions = (
            "、".join(product.shipping_regions) if is_zh else ", ".join(product.shipping_regions)
        )
        return (
            f"这款商品标注可配送至{regions}。具体地址仍需在结账时确认。"
            if is_zh
            else f"This product is listed for delivery to {regions}. Your exact address "
            "still needs to be confirmed at checkout."
        )
    return None


_DIMENSION_ALIASES = {
    "height": ("height", "tall", "身高", "高度"),
    "bra_size": ("bra size", "cup size", "cup", "罩杯", "胸罩"),
    "oral_depth": ("oral depth", "mouth depth", "口腔深度", "口深"),
    "vaginal_depth": ("vaginal depth", "vagina depth", "阴道深度"),
    "anal_depth": ("anal depth", "anus depth", "肛门深度"),
    "package_size": ("package size", "package dimensions", "包装尺寸", "包装大小"),
    "bust": ("bust", "chest", "胸围"),
    "waist": ("waist", "腰围"),
    "hips": ("hips", "hip", "臀围"),
    "shoulder": ("shoulder", "肩宽"),
    "foot": ("foot", "feet", "脚长", "足长"),
}


def _dimension_kind(value: str) -> str | None:
    normalized = " ".join(value.casefold().replace("_", " ").replace("-", " ").split())
    for kind, aliases in _DIMENSION_ALIASES.items():
        if any(alias in normalized for alias in aliases):
            return kind
    return None


def _requested_dimensions(
    question: str,
    dimensions: dict[str, str],
) -> tuple[tuple[str, str], ...]:
    normalized_question = " ".join(question.casefold().split())
    requested_kinds = {
        kind
        for kind, aliases in _DIMENSION_ALIASES.items()
        if any(alias in normalized_question for alias in aliases)
    }
    if not requested_kinds:
        return tuple(dimensions.items())
    selected = tuple(
        (key, value) for key, value in dimensions.items() if _dimension_kind(key) in requested_kinds
    )
    return selected or tuple(dimensions.items())


def _readable_measurement(value: str) -> str:
    normalized = re.sub(r"\s*/\s*", " / ", value.strip())
    normalized = re.sub(r"(?<=\d)(?=(?:cm|mm|kg|lb|in)\b)", " ", normalized, flags=re.I)
    parts = normalized.split(" / ")
    return f"{parts[0]} ({' / '.join(parts[1:])})" if len(parts) > 1 else normalized


def _is_us_stock_listing(product: ProductSnapshot) -> bool:
    values = (product.name, product.shipping_warehouse or "")
    return any(
        re.search(r"\b(?:us|usa|united states)\s*(?:warehouse|stock)\b", value, re.I)
        or re.search(r"\b(?:warehouse|stock)\s*(?:in\s*)?(?:us|usa|united states)\b", value, re.I)
        for value in values
    )


def _discount_guidance(language: str) -> str:
    if language.casefold().startswith("zh"):
        return "我不能确认优惠码当前有效。请在结账页核验，或联系人工客服确认。"
    return (
        "I cannot confirm that a promotion code is currently valid. Please verify it at "
        "checkout or contact a human support agent."
    )


def _stale_message(language: str, _url: str) -> str:
    if language.casefold().startswith("zh"):
        return (
            "当前商品信息已超过可回答时限，我不会提供可能过期的数字。请查看商品页或联系人工客服。"
        )
    return (
        "The available product information is outside the freshness window, so I will not "
        "provide potentially outdated details. Please check the product page or contact support."
    )


def _price_label(price: str, currency: str | None) -> str:
    symbols = {"USD": "$", "EUR": "€", "GBP": "£", "CNY": "¥", "JPY": "¥"}
    normalized_currency = (currency or "").upper()
    return f"{symbols.get(normalized_currency, normalized_currency + ' ')}{price}".strip()


def _stock_label(value: str, is_zh: bool) -> str:
    normalized = re.sub(r"^https?://schema.org/", "", value, flags=re.IGNORECASE)
    labels = {
        "instock": ("有货", "in stock"),
        "outofstock": ("缺货", "out of stock"),
        "preorder": ("可预订", "pre-order"),
        "backorder": ("可延期交货", "back-order"),
        "discontinued": ("已停止销售", "discontinued"),
    }
    pair = labels.get(normalized.casefold())
    return pair[0 if is_zh else 1] if pair else normalized
