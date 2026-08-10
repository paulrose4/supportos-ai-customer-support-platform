import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation

from app.domain.models.product_catalog import ProductSnapshot
from app.domain.models.recommendation import (
    ProductRecommendationConstraint,
    ProductRecommendationMode,
    ProductRecommendationPlan,
    RecommendationConstraintStrength,
)
from app.domain.models.sales import SalesMemoryFact

_RELATIVE_TERMS = (
    "alternative",
    "similar",
    "replacement",
    "substitute",
    "cheaper",
    "less expensive",
    "lighter",
    "类似",
    "相似",
    "替代",
    "换一款",
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
_CHEAPER_TERMS = (
    "cheaper",
    "less expensive",
    "便宜一点",
    "更便宜",
    "günstiger",
    "moins cher",
    "más barato",
)
_LIGHTER_TERMS = (
    "lighter",
    "lightweight",
    "light weight",
    "轻一点",
    "更轻",
    "轻便",
    "轻量",
    "leichter",
    "plus léger",
    "más ligero",
)
_COMPACT_TERMS = (
    "compact",
    "easy to store",
    "small storage",
    "容易收纳",
    "方便收纳",
    "收纳方便",
)
_SOFT_MARKERS = (
    "prefer",
    "preferred",
    "ideally",
    "if possible",
    "偏好",
    "最好",
    "尽量",
    "比较喜欢",
    "优先",
)
_MATERIAL_TERMS = {
    "TPE": ("tpe", "thermoplastic elastomer", "热塑性弹性体"),
    "SILICONE": ("silicone", "silikon", "silicona", "硅胶"),
    "PVC": ("pvc", "polyvinyl chloride", "聚氯乙烯"),
}
_NEGATIVE_PREFIXES = (
    "not ",
    "no ",
    "without ",
    "don't want ",
    "do not want ",
    "exclude ",
    "nicht ",
    "kein ",
    "keine ",
    "sin ",
    "pas de ",
    "不要",
    "不想要",
    "排除",
    "不考虑",
)
_OUT_OF_STOCK_TERMS = (
    "outofstock",
    "out of stock",
    "soldout",
    "sold out",
    "unavailable",
    "无库存",
    "缺货",
    "售罄",
    "下架",
)
_IN_STOCK_TERMS = ("instock", "in stock", "available", "有库存", "现货")
_CURRENCY_TERMS = {
    "USD": ("$", "usd", "dollar", "美元", "美金"),
    "EUR": ("€", "eur", "euro", "欧元"),
    "GBP": ("£", "gbp", "pound", "英镑"),
    "CNY": ("¥", "￥", "cny", "rmb", "人民币"),
}
_REGION_ALIASES = {
    "US": ("us", "usa", "united states", "美国"),
    "CA": ("ca", "canada", "加拿大"),
    "EU": ("eu", "europe", "european union", "欧盟", "欧洲"),
    "AU": ("au", "australia", "澳大利亚", "澳洲"),
    "GB": ("gb", "uk", "united kingdom", "英国"),
}


def build_product_recommendation_plan(
    question: str,
    *,
    memory_facts: Sequence[SalesMemoryFact] = (),
    destination: str | None = None,
    source_product: ProductSnapshot | None = None,
) -> ProductRecommendationPlan:
    normalized = " ".join(question.casefold().split())
    constraints: list[ProductRecommendationConstraint] = []
    constraints.extend(_message_constraints(question, normalized))
    constraints.extend(_memory_constraints(memory_facts, constraints))
    if source_product is not None:
        mode = ProductRecommendationMode.SUBSTITUTE
        constraints.extend(_relative_constraints(normalized, source_product))
    elif any(item.strength is RecommendationConstraintStrength.HARD for item in constraints):
        mode = ProductRecommendationMode.EXACT_MATCH
    else:
        mode = ProductRecommendationMode.PREFERENCE
    return ProductRecommendationPlan(
        mode=mode,
        query_text=question.strip(),
        constraints=_unique_constraints(constraints),
        destination=destination.strip().upper() if destination else None,
        source_product_key=source_product.product_key if source_product else None,
    )


def is_relative_product_request(question: str) -> bool:
    normalized = " ".join(question.casefold().split())
    return any(term in normalized for term in _RELATIVE_TERMS)


def product_passes_hard_constraints(
    product: ProductSnapshot,
    plan: ProductRecommendationPlan,
    *,
    source_product: ProductSnapshot | None = None,
) -> tuple[bool, tuple[str, ...], tuple[str, ...]]:
    missing: list[str] = []
    violations: list[str] = []
    if is_known_out_of_stock(product.stock_status):
        violations.append("out_of_stock")
    if plan.destination and product.shipping_regions:
        if not ships_to_destination(product.shipping_regions, plan.destination):
            violations.append("shipping_region")
    elif plan.destination:
        missing.append("shipping_regions")
    for constraint in plan.hard_constraints:
        value = constraint.value
        if constraint.field == "max_price":
            price = decimal_value(product.price)
            if price is None:
                missing.append("price")
            elif price > Decimal(value):
                violations.append("price")
        elif constraint.field == "currency":
            currency = (product.currency or "").strip().upper()
            if not currency:
                missing.append("currency")
            elif currency != value.upper():
                violations.append("currency")
        elif constraint.field == "max_weight_kg":
            weight = product_weight_kg(product)
            if weight is None:
                missing.append("weight")
            elif weight > float(value):
                violations.append("weight")
        elif constraint.field in {"min_height_cm", "max_height_cm"}:
            height = product_height_cm(product)
            if height is None:
                missing.append("height")
            elif constraint.field == "min_height_cm" and height < float(value):
                violations.append("height")
            elif constraint.field == "max_height_cm" and height > float(value):
                violations.append("height")
        elif constraint.field == "material":
            if not product.material:
                missing.append("material")
            elif value.casefold() not in product.material.casefold():
                violations.append("material")
        elif constraint.field == "excluded_material":
            if product.material and value.casefold() in product.material.casefold():
                violations.append("excluded_material")
        elif constraint.field == "required_term":
            if value.casefold() not in product_search_text(product).casefold():
                violations.append("required_term")
        elif constraint.field == "excluded_term":
            if value.casefold() in product_search_text(product).casefold():
                violations.append("excluded_term")
        elif constraint.field == "cheaper_than_source":
            source_price = decimal_value(source_product.price if source_product else None)
            price = decimal_value(product.price)
            if source_price is None or price is None:
                missing.append("price")
            elif not same_currency(source_product, product) or price >= source_price:
                violations.append("cheaper_than_source")
        elif constraint.field == "lighter_than_source":
            source_weight = product_weight_kg(source_product) if source_product else None
            weight = product_weight_kg(product)
            if source_weight is None or weight is None:
                missing.append("weight")
            elif weight >= source_weight:
                violations.append("lighter_than_source")
    return (
        not violations and not missing,
        tuple(dict.fromkeys(violations)),
        tuple(dict.fromkeys(missing)),
    )


def structured_product_similarity(
    source: ProductSnapshot | None,
    candidate: ProductSnapshot,
) -> tuple[float, float, tuple[str, ...], tuple[str, ...]]:
    if source is None:
        coverage = product_evidence_coverage(candidate)
        return coverage, coverage, (), _missing_profile_fields(candidate)
    comparisons: list[tuple[float, float, str]] = []
    if source.material and candidate.material:
        comparisons.append(
            (0.20, float(source.material.casefold() == candidate.material.casefold()), "material")
        )
    if source.brand and candidate.brand:
        comparisons.append(
            (0.10, float(source.brand.casefold() == candidate.brand.casefold()), "brand")
        )
    source_height = product_height_cm(source)
    candidate_height = product_height_cm(candidate)
    if source_height is not None and candidate_height is not None:
        comparisons.append(
            (0.25, numeric_similarity(source_height, candidate_height, 12.0), "height")
        )
    source_weight = product_weight_kg(source)
    candidate_weight = product_weight_kg(candidate)
    if source_weight is not None and candidate_weight is not None:
        comparisons.append(
            (0.20, numeric_similarity(source_weight, candidate_weight, 10.0), "weight")
        )
    dimension_score = matching_dimension_similarity(source.dimensions, candidate.dimensions)
    if dimension_score is not None:
        comparisons.append((0.15, dimension_score, "dimensions"))
    source_price = decimal_value(source.price)
    candidate_price = decimal_value(candidate.price)
    if (
        source_price is not None
        and candidate_price is not None
        and same_currency(source, candidate)
    ):
        comparisons.append((0.10, replacement_price_similarity(source, candidate), "price"))
    available_weight = sum(weight for weight, _score, _field in comparisons)
    known_similarity = (
        sum(weight * score for weight, score, _field in comparisons) / available_weight
        if available_weight
        else 0.0
    )
    coverage = min(1.0, available_weight)
    score = known_similarity * (0.60 + 0.40 * coverage)
    matched = tuple(field for _weight, value, field in comparisons if value >= 0.70)
    return score, coverage, matched, _missing_profile_fields(candidate)


def product_preference_score(
    product: ProductSnapshot,
    plan: ProductRecommendationPlan,
) -> tuple[float, tuple[str, ...], tuple[str, ...]]:
    scores: list[tuple[float, float]] = []
    matched: list[str] = []
    missing: list[str] = []
    for constraint in plan.soft_constraints:
        score: float | None = None
        if constraint.field == "material":
            if product.material:
                score = float(constraint.value.casefold() in product.material.casefold())
            else:
                missing.append("material")
        elif constraint.field == "lightweight":
            weight = product_weight_kg(product)
            if weight is None:
                missing.append("weight")
            else:
                score = math.exp(-weight / 35.0)
        elif constraint.field == "compact":
            volume = product_volume_cm3(product)
            if volume is None:
                missing.append("dimensions")
            else:
                score = math.exp(-volume / 750_000.0)
        elif constraint.field == "preferred_term":
            score = float(constraint.value.casefold() in product_search_text(product).casefold())
        if score is not None:
            scores.append((score, max(0.05, min(1.0, constraint.weight))))
            if score >= 0.60:
                matched.append(_constraint_reason(constraint))
    if not scores:
        return 0.0, tuple(matched), tuple(dict.fromkeys(missing))
    total_weight = sum(weight for _score, weight in scores)
    return (
        sum(score * weight for score, weight in scores) / total_weight,
        tuple(matched),
        tuple(dict.fromkeys(missing)),
    )


def replacement_price_similarity(source: ProductSnapshot, candidate: ProductSnapshot) -> float:
    source_price = decimal_value(source.price)
    candidate_price = decimal_value(candidate.price)
    if source_price is None or source_price <= 0 or candidate_price is None:
        return 0.0
    if not same_currency(source, candidate):
        return 0.0
    ratio = float((candidate_price - source_price) / source_price)
    scale = 0.30 if ratio <= 0 else 0.12
    return math.exp(-abs(ratio) / scale)


def product_business_score(product: ProductSnapshot) -> float:
    stock = (product.stock_status or "").casefold()
    if any(term in stock for term in _IN_STOCK_TERMS):
        stock_score = 1.0
    elif not stock:
        stock_score = 0.45
    else:
        stock_score = 0.65
    warehouse_score = 0.80 if product.shipping_warehouse else 0.50
    return 0.75 * stock_score + 0.25 * warehouse_score


def product_evidence_coverage(product: ProductSnapshot) -> float:
    facts = (
        bool(product.material),
        bool(product.brand),
        product_weight_kg(product) is not None,
        product_height_cm(product) is not None,
        decimal_value(product.price) is not None and bool(product.currency),
        bool(product.stock_status),
    )
    return sum(facts) / len(facts)


def product_search_text(product: ProductSnapshot) -> str:
    dimensions = " ".join(f"{key} {value}" for key, value in sorted(product.dimensions.items()))
    return " ".join(
        value
        for value in (
            product.name,
            product.sku or "",
            product.mpn or "",
            product.brand or "",
            product.material or "",
            product.weight or "",
            dimensions,
        )
        if value
    )


def bm25_scores(query: str, documents: Sequence[str]) -> list[float]:
    tokenized_documents = [recommendation_tokens(document) for document in documents]
    query_tokens = recommendation_tokens(query)
    if not tokenized_documents or not query_tokens:
        return [0.0] * len(documents)
    average_length = sum(len(tokens) for tokens in tokenized_documents) / len(tokenized_documents)
    document_frequency: Counter[str] = Counter()
    for tokens in tokenized_documents:
        document_frequency.update(set(tokens))
    results: list[float] = []
    total_documents = len(tokenized_documents)
    for tokens in tokenized_documents:
        frequencies = Counter(tokens)
        score = 0.0
        for token in query_tokens:
            frequency = frequencies[token]
            if not frequency:
                continue
            frequency_in_documents = document_frequency[token]
            inverse_document_frequency = math.log(
                1
                + (total_documents - frequency_in_documents + 0.5) / (frequency_in_documents + 0.5)
            )
            denominator = frequency + 1.5 * (
                1 - 0.75 + 0.75 * len(tokens) / max(1.0, average_length)
            )
            score += inverse_document_frequency * frequency * 2.5 / denominator
        results.append(score)
    maximum = max(results, default=0.0)
    return [score / maximum if maximum else 0.0 for score in results]


def reciprocal_rank_fusion(score_lists: Sequence[Sequence[float]], *, k: int = 60) -> list[float]:
    if not score_lists:
        return []
    size = len(score_lists[0])
    fused = [0.0] * size
    for scores in score_lists:
        if len(scores) != size:
            raise ValueError("all score lists must have the same length")
        if not any(score > 0 for score in scores):
            continue
        ranking = sorted(range(size), key=lambda index: (-scores[index], index))
        for rank, index in enumerate(ranking, start=1):
            fused[index] += 1 / (k + rank)
    maximum = max(fused, default=0.0)
    return [value / maximum if maximum else 0.0 for value in fused]


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return max(0.0, min(1.0, dot / (left_norm * right_norm)))


def recommendation_tokens(text: str) -> list[str]:
    lowered = text.casefold()
    words = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", lowered)
    return [word for word in words if len(word) > 1 or "\u4e00" <= word <= "\u9fff"]


def product_weight_kg(product: ProductSnapshot | None) -> float | None:
    if product is None or not product.weight:
        return None
    number = _first_number(product.weight)
    if number is None:
        return None
    normalized = product.weight.casefold()
    if "lb" in normalized or "磅" in normalized:
        return number * 0.45359237
    return number


def product_height_cm(product: ProductSnapshot) -> float | None:
    for key, value in product.dimensions.items():
        normalized_key = key.casefold()
        if any(term in normalized_key for term in ("height", "length", "身高", "高度", "长度")):
            return _length_cm(value)
    return None


def product_volume_cm3(product: ProductSnapshot) -> float | None:
    values = [_length_cm(value) for value in product.dimensions.values()]
    known = [value for value in values if value is not None]
    if len(known) < 3:
        return None
    return known[0] * known[1] * known[2]


def decimal_value(value: str | None) -> Decimal | None:
    if not value:
        return None
    match = re.search(r"\d+(?:\.\d+)?", value.replace(",", ""))
    if match is None:
        return None
    try:
        return Decimal(match.group(0))
    except InvalidOperation:
        return None


def same_currency(left: ProductSnapshot | None, right: ProductSnapshot) -> bool:
    if left is None:
        return False
    left_currency = (left.currency or "").strip().upper()
    right_currency = (right.currency or "").strip().upper()
    return bool(left_currency and right_currency and left_currency == right_currency)


def is_known_out_of_stock(value: str | None) -> bool:
    normalized = (value or "").casefold().replace("_", "")
    return any(term.replace(" ", "") in normalized.replace(" ", "") for term in _OUT_OF_STOCK_TERMS)


def ships_to_destination(regions: Sequence[str], destination: str) -> bool:
    normalized_regions = " ".join(regions).casefold()
    aliases = _REGION_ALIASES.get(destination.upper(), (destination.casefold(),))
    return any(
        re.search(rf"(?<![a-z]){re.escape(alias.casefold())}(?![a-z])", normalized_regions)
        for alias in aliases
    )


def hard_constraint_labels(plan: ProductRecommendationPlan) -> tuple[str, ...]:
    labels: list[str] = []
    for item in plan.hard_constraints:
        if item.field == "max_price":
            labels.append(f"price<={item.value}")
        elif item.field == "currency":
            labels.append(f"currency={item.value}")
        elif item.field == "max_weight_kg":
            labels.append(f"weight<={item.value}kg")
        elif item.field == "min_height_cm":
            labels.append(f"height>={item.value}cm")
        elif item.field == "max_height_cm":
            labels.append(f"height<={item.value}cm")
        elif item.field == "excluded_material":
            labels.append(f"material!={item.value}")
        else:
            labels.append(f"{item.field}{item.operator}{item.value}")
    return tuple(labels)


def _message_constraints(
    question: str,
    normalized: str,
) -> list[ProductRecommendationConstraint]:
    constraints: list[ProductRecommendationConstraint] = []
    budget = _budget_match(question)
    if budget and re.match(
        r"\s*(?:kg|公斤|千克|lb|lbs|磅|cm|厘米|mm|毫米|in|inch|inches|英寸)\b",
        question[budget.end() :],
        flags=re.IGNORECASE,
    ):
        budget = None
    if budget:
        constraints.append(_constraint("max_price", "lte", budget.group("amount").replace(",", "")))
        currency = _currency_from_match(budget, normalized)
        if currency:
            constraints.append(_constraint("currency", "eq", currency))
    max_weight = re.search(
        r"(?i)(?:under|below|no more than|maximum|max\.?|不超过|最多|低于)\s*"
        r"(?P<amount>\d+(?:\.\d+)?)\s*(?P<unit>kg|公斤|千克|lb|lbs|磅)\b",
        question,
    )
    if max_weight:
        amount = float(max_weight.group("amount"))
        if max_weight.group("unit").casefold() in {"lb", "lbs", "磅"}:
            amount *= 0.45359237
        constraints.append(
            _constraint("max_weight_kg", "lte", f"{amount:.4f}".rstrip("0").rstrip("."))
        )
    height = re.search(
        r"(?i)(\d+(?:\.\d+)?)\s*(?:to|到|至|[-~～])\s*(\d+(?:\.\d+)?)\s*(?:cm|厘米)",
        question,
    )
    if height:
        left, right = sorted((float(height.group(1)), float(height.group(2))))
        constraints.extend(
            (
                _constraint("min_height_cm", "gte", f"{left:g}"),
                _constraint("max_height_cm", "lte", f"{right:g}"),
            )
        )
    else:
        max_height = re.search(
            r"(?i)(?:under|below|no more than|maximum|max\.?|不超过|最高|低于)\s*"
            r"(?P<amount>\d+(?:\.\d+)?)\s*(?:cm|厘米)\b",
            question,
        )
        min_height = re.search(
            r"(?i)(?:at least|minimum|min\.?|no less than|至少|最低|不少于)\s*"
            r"(?P<amount>\d+(?:\.\d+)?)\s*(?:cm|厘米)\b",
            question,
        )
        if max_height:
            constraints.append(_constraint("max_height_cm", "lte", max_height.group("amount")))
        if min_height:
            constraints.append(_constraint("min_height_cm", "gte", min_height.group("amount")))
    for material, terms in _MATERIAL_TERMS.items():
        if not any(term in normalized for term in terms):
            continue
        excluded = any(
            f"{prefix}{term}" in normalized for prefix in _NEGATIVE_PREFIXES for term in terms
        )
        if excluded:
            constraints.append(_constraint("excluded_material", "neq", material))
            continue
        strength = (
            RecommendationConstraintStrength.SOFT
            if any(marker in normalized for marker in _SOFT_MARKERS)
            else RecommendationConstraintStrength.HARD
        )
        constraints.append(_constraint("material", "contains", material, strength=strength))
    if any(term in normalized for term in _LIGHTER_TERMS):
        constraints.append(
            _constraint(
                "lightweight",
                "prefer",
                "true",
                strength=RecommendationConstraintStrength.SOFT,
            )
        )
    if any(term in normalized for term in _COMPACT_TERMS):
        constraints.append(
            _constraint("compact", "prefer", "true", strength=RecommendationConstraintStrength.SOFT)
        )
    for match in re.finditer(r"(?:不要|排除|without|exclude)\s*([^，。；,;\s]+)", normalized):
        term = match.group(1).strip()
        if term and not any(term in aliases for aliases in _MATERIAL_TERMS.values()):
            constraints.append(_constraint("excluded_term", "not_contains", term))
    return constraints


def _memory_constraints(
    memory_facts: Sequence[SalesMemoryFact],
    existing: Sequence[ProductRecommendationConstraint],
) -> list[ProductRecommendationConstraint]:
    existing_fields = {item.field for item in existing}
    constraints: list[ProductRecommendationConstraint] = []
    for fact in memory_facts:
        if fact.status.value != "confirmed":
            continue
        if fact.key == "budget_max" and "max_price" not in existing_fields:
            constraints.append(_constraint("max_price", "lte", fact.value, source="memory"))
        elif fact.key == "budget_currency" and "currency" not in existing_fields:
            constraints.append(_constraint("currency", "eq", fact.value.upper(), source="memory"))
        elif fact.key == "max_weight" and "max_weight_kg" not in existing_fields:
            weight = _weight_text_kg(fact.value)
            if weight is not None:
                constraints.append(
                    _constraint("max_weight_kg", "lte", f"{weight:g}", source="memory")
                )
        elif fact.key == "material" and "material" not in existing_fields:
            constraints.append(
                _constraint(
                    "material",
                    "contains",
                    fact.value,
                    strength=RecommendationConstraintStrength.SOFT,
                    source="memory",
                )
            )
        elif fact.key == "excluded_material":
            constraints.append(_constraint("excluded_material", "neq", fact.value, source="memory"))
        elif fact.key == "handling" and fact.value == "lightweight":
            constraints.append(
                _constraint(
                    "lightweight",
                    "prefer",
                    "true",
                    strength=RecommendationConstraintStrength.SOFT,
                    source="memory",
                )
            )
        elif fact.key == "storage" and fact.value == "compact":
            constraints.append(
                _constraint(
                    "compact",
                    "prefer",
                    "true",
                    strength=RecommendationConstraintStrength.SOFT,
                    source="memory",
                )
            )
        elif fact.key == "excluded_feature":
            constraints.append(
                _constraint("excluded_term", "not_contains", fact.value, source="memory")
            )
        elif fact.key == "feature":
            constraints.append(
                _constraint(
                    "preferred_term",
                    "contains",
                    fact.value,
                    strength=RecommendationConstraintStrength.SOFT,
                    source="memory",
                )
            )
    return constraints


def _relative_constraints(
    normalized: str,
    source: ProductSnapshot,
) -> list[ProductRecommendationConstraint]:
    constraints: list[ProductRecommendationConstraint] = []
    if any(term in normalized for term in _CHEAPER_TERMS):
        constraints.append(_constraint("cheaper_than_source", "lt", source.product_key))
    if any(term in normalized for term in _LIGHTER_TERMS) and "lightweight" not in normalized:
        constraints.append(_constraint("lighter_than_source", "lt", source.product_key))
    return constraints


def _constraint(
    field: str,
    operator: str,
    value: str,
    *,
    strength: RecommendationConstraintStrength = RecommendationConstraintStrength.HARD,
    source: str = "current_message",
) -> ProductRecommendationConstraint:
    return ProductRecommendationConstraint(field, operator, str(value), strength, source)


def _unique_constraints(
    constraints: Sequence[ProductRecommendationConstraint],
) -> tuple[ProductRecommendationConstraint, ...]:
    unique: dict[tuple[str, str, str], ProductRecommendationConstraint] = {}
    for item in constraints:
        key = (item.field, item.operator, item.value.casefold())
        existing = unique.get(key)
        if existing is None or (
            existing.strength is RecommendationConstraintStrength.SOFT
            and item.strength is RecommendationConstraintStrength.HARD
        ):
            unique[key] = item
    return tuple(unique.values())


def _constraint_reason(constraint: ProductRecommendationConstraint) -> str:
    return {
        "material": "material_match",
        "lightweight": "lightweight_option",
        "compact": "compact_option",
        "preferred_term": f"feature_match:{constraint.value}",
    }.get(constraint.field, f"preference_match:{constraint.field}")


def _missing_profile_fields(product: ProductSnapshot) -> tuple[str, ...]:
    missing: list[str] = []
    if not product.material:
        missing.append("material")
    if product_weight_kg(product) is None:
        missing.append("weight")
    if not product.dimensions:
        missing.append("dimensions")
    if decimal_value(product.price) is None:
        missing.append("price")
    if not product.stock_status:
        missing.append("stock")
    return tuple(missing)


def matching_dimension_similarity(
    source: Mapping[str, str],
    candidate: Mapping[str, str],
) -> float | None:
    candidate_by_key = {key.casefold(): value for key, value in candidate.items()}
    scores: list[float] = []
    for key, source_value in source.items():
        candidate_value = candidate_by_key.get(key.casefold())
        if candidate_value is None:
            continue
        left = _length_cm(source_value)
        right = _length_cm(candidate_value)
        if left is not None and right is not None:
            scores.append(numeric_similarity(left, right, 10.0))
    return sum(scores) / len(scores) if scores else None


def numeric_similarity(source: float, candidate: float, scale: float) -> float:
    return math.exp(-abs(source - candidate) / scale)


def _budget_match(message: str) -> re.Match[str] | None:
    return re.search(
        r"(?ix)(?:"
        r"budget(?:\s+(?:is|of|around|about|approximately))?\s*(?:is|=|:)?|"
        r"under|below|less\s+than|up\s+to|"
        r"预算(?:大约|大概|约|是|为|在)?\s*(?:是|为|:|：)?|"
        r"不超过|低于|最多|以内|unter|"
        r"presupuesto(?:\s+es|\s+de)?|budget(?:\s+est|\s+de)?"
        r")\s*"
        r"(?P<prefix>[$€£¥￥])?\s*"
        r"(?P<amount>\d{2,6}(?:,\d{3})*(?:\.\d{1,2})?)\s*"
        r"(?P<suffix>usd|eur|gbp|cny|rmb|dollars?|euros?|美元|美金|欧元|英镑|人民币)?",
        message,
    )


def _currency_from_match(match: re.Match[str], normalized: str) -> str | None:
    value = f"{match.group('prefix') or ''} {match.group('suffix') or ''} {normalized}"
    for currency, terms in _CURRENCY_TERMS.items():
        if any(term in value for term in terms):
            return currency
    return None


def _weight_text_kg(value: str) -> float | None:
    number = _first_number(value)
    if number is None:
        return None
    if "lb" in value.casefold() or "磅" in value:
        return number * 0.45359237
    return number


def _length_cm(value: str) -> float | None:
    number = _first_number(value)
    if number is None:
        return None
    normalized = value.casefold()
    if re.search(r"(?<!c)\bmm\b|毫米", normalized):
        return number / 10.0
    if re.search(r"\b(?:in|inch|inches)\b|英寸", normalized):
        return number * 2.54
    if (
        re.search(r"(?<!c)\bm\b|米", normalized)
        and "cm" not in normalized
        and "厘米" not in normalized
    ):
        return number * 100.0
    return number


def _first_number(value: str) -> float | None:
    match = re.search(r"\d+(?:\.\d+)?", value.replace(",", ""))
    return float(match.group(0)) if match else None
