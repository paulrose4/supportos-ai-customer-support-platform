import re
from collections.abc import Iterable
from dataclasses import replace
from decimal import Decimal, InvalidOperation

from app.domain.models import (
    ConversationSummary,
    ConversationTurn,
    ConversationWorkingMemory,
    DurableCustomerMemory,
    RecommendedProduct,
    ReferenceResolution,
    ResponseKind,
)
from app.domain.rules.care import classify_care_risk
from app.domain.rules.conversation import extract_product_skus
from app.domain.rules.intent import classify_support_intent
from app.domain.rules.links import classify_link_display_intent
from app.domain.rules.sales import (
    classify_primary_objection,
    classify_sales_stage,
    extract_interaction_preferences,
    extract_question_ledger_items,
    extract_sales_memory_facts,
    merge_interaction_preferences,
    recent_response_phrases,
)

_MEANING_REFERENCES = (
    "what's the meaning",
    "what is the meaning",
    "what does it mean",
    "什么意思",
    "是什么意思",
    "意味は",
    "was bedeutet",
    "qué significa",
    "que signifie",
)
_ENTITY_REFERENCES = (
    "this one",
    "this product",
    "this model",
    "that one",
    "that model",
    "the model",
    "your product",
    "the product",
    "this product",
    "that product",
    "这个",
    "这款",
    "那个型号",
    "このモデル",
    "dieses modell",
    "este modelo",
    "ce modèle",
)
_PAIR_REFERENCES = (
    "these two",
    "those two",
    "both models",
    "both options",
    "这两款",
    "这两个",
    "两款商品",
    "beide modelle",
    "ces deux",
    "estos dos",
)
_COUNTRY_TERMS = {
    "US": ("united states", "usa", "u.s.", "美国"),
    "CA": ("canada", "加拿大"),
    "GB": ("united kingdom", "uk", "u.k.", "英国"),
    "AU": ("australia", "澳大利亚", "澳洲"),
    "DE": ("germany", "德国"),
    "FR": ("france", "法国"),
    "JP": ("japan", "日本"),
    "CN": ("china", "中国"),
}
_COUNTRY_CURRENCIES = {
    "US": "USD",
    "CA": "CAD",
    "GB": "GBP",
    "AU": "AUD",
    "DE": "EUR",
    "FR": "EUR",
    "JP": "JPY",
    "CN": "CNY",
}
_HUMAN_TERMS = ("human agent", "real person", "人工客服", "转人工", "真人客服")
_ORDINAL_REFERENCES = (
    (("first", "1st", "第一款", "第一个", "erste", "premier", "primero"), 0),
    (("second", "2nd", "第二款", "第二个", "zweite", "deuxième", "segundo"), 1),
    (("third", "3rd", "第三款", "第三个", "dritte", "troisième", "tercero"), 2),
)
_CHEAPER_REFERENCES = (
    "cheaper",
    "less expensive",
    "lower price",
    "便宜一点",
    "更便宜",
    "günstiger",
    "moins cher",
    "más barato",
)
_LIGHTER_REFERENCES = (
    "lighter",
    "lower weight",
    "轻一点",
    "更轻",
    "leichter",
    "plus léger",
    "más ligero",
)


def resolve_memory_reference(
    *,
    message: str,
    memory: ConversationWorkingMemory,
    language: str,
) -> ReferenceResolution:
    normalized = " ".join(message.casefold().split())
    if any(term in normalized for term in _PAIR_REFERENCES):
        if len(memory.candidate_product_skus) >= 2:
            references = "\n".join(
                f"PRODUCT_REFERENCE: {sku}" for sku in memory.candidate_product_skus[:2]
            )
            return ReferenceResolution(resolved_message=f"{message.strip()}\n{references}")
        return ReferenceResolution(
            resolved_message=message.strip(),
            clarification=_missing_comparison_reference_clarification(language),
        )
    candidate_reference = _resolve_candidate_reference(normalized, memory)
    if candidate_reference:
        return ReferenceResolution(
            resolved_message=f"{message.strip()}\nPRODUCT_REFERENCE: {candidate_reference}"
        )
    relative_reference = any(
        term in normalized for terms, _ in _ORDINAL_REFERENCES for term in terms
    ) or any(term in normalized for term in (*_CHEAPER_REFERENCES, *_LIGHTER_REFERENCES))
    if relative_reference and classify_support_intent(message).value != "product_recommendation":
        return ReferenceResolution(
            resolved_message=message.strip(),
            clarification=_missing_comparison_reference_clarification(language),
        )
    meaning_reference = any(term in normalized for term in _MEANING_REFERENCES)
    entity_reference = meaning_reference or any(term in normalized for term in _ENTITY_REFERENCES)
    if not entity_reference:
        return ReferenceResolution(message.strip())
    if (
        not meaning_reference
        and not memory.active_product_sku
        and not memory.candidate_product_skus
        and not _is_short_entity_reference(normalized)
    ):
        return ReferenceResolution(message.strip())
    if memory.pending_product_sku:
        return ReferenceResolution(
            resolved_message=message.strip(),
            clarification=_pending_product_clarification(memory.pending_product_sku, language),
        )
    if memory.active_product_sku:
        if meaning_reference:
            return ReferenceResolution(
                resolved_message=message.strip(),
                clarification=_active_product_clarification(memory.active_product_sku, language),
            )
        return ReferenceResolution(
            resolved_message=f"{message.strip()}\nPRODUCT_REFERENCE: {memory.active_product_sku}"
        )
    if len(memory.candidate_product_skus) == 1:
        return ReferenceResolution(
            resolved_message=(
                f"{message.strip()}\nPRODUCT_REFERENCE: {memory.candidate_product_skus[0]}"
            )
        )
    if len(memory.candidate_product_skus) > 1:
        return ReferenceResolution(
            message.strip(),
            _candidate_product_clarification(
                memory.candidate_product_skus,
                language,
                memory.candidate_product_labels,
            ),
        )
    return ReferenceResolution(message.strip(), _missing_reference_clarification(language))


def update_working_memory(
    *,
    current: ConversationWorkingMemory,
    user_message: str,
    resolved_message: str,
    response_kind: ResponseKind,
    response_language: str,
    candidate_product_skus: tuple[str, ...] = (),
    answer_missing_fields: tuple[str, ...] = (),
    recommended_products: tuple[RecommendedProduct, ...] = (),
    assistant_message: str = "",
) -> ConversationWorkingMemory:
    skus = extract_product_skus(user_message)
    intent = classify_conversation_intent(resolved_message)
    active_product_sku = current.active_product_sku
    pending_product_sku = current.pending_product_sku
    unresolved_question = current.unresolved_question
    candidates = [] if candidate_product_skus else list(current.candidate_product_skus)
    for sku in candidate_product_skus:
        normalized_sku = sku.strip().upper()
        if normalized_sku and normalized_sku not in candidates:
            candidates.append(normalized_sku)
    candidates = candidates[-5:]
    relative_candidate_selected = bool(recommended_products) and any(
        term in user_message.casefold() for term in (*_CHEAPER_REFERENCES, *_LIGHTER_REFERENCES)
    )
    structured_candidates = (
        current.candidate_products
        if relative_candidate_selected and current.candidate_products
        else (recommended_products[:5] if recommended_products else current.candidate_products)
    )
    if structured_candidates:
        candidates = [item.sku for item in structured_candidates]
        candidate_labels = tuple(_recommended_product_label(item) for item in structured_candidates)
    else:
        candidate_labels = () if candidate_product_skus else current.candidate_product_labels
    if skus:
        referenced_sku = skus[0]
        if response_kind is ResponseKind.ANSWER:
            active_product_sku = referenced_sku
            pending_product_sku = None
            unresolved_question = None
        else:
            pending_product_sku = referenced_sku
            unresolved_question = user_message.strip()[:500]
    elif response_kind is ResponseKind.ANSWER and pending_product_sku:
        pending_product_sku = None
        unresolved_question = None
    if relative_candidate_selected and response_kind is ResponseKind.ANSWER:
        active_product_sku = recommended_products[0].sku
    if len(candidates) == 1 and response_kind is ResponseKind.ANSWER:
        active_product_sku = active_product_sku or candidates[0]
    country_code = _country_from_message(user_message) or current.country_code
    currency = _currency_from_message(user_message, country_code) or current.currency
    confirmed_fields = set(current.confirmed_fields)
    if active_product_sku:
        confirmed_fields.add("product")
    if country_code:
        confirmed_fields.add("country")
    if currency:
        confirmed_fields.add("currency")
    preference_facts = list(current.preference_facts)
    for fact in extract_sales_memory_facts(
        user_message,
        source_revision=current.revision + 1,
    ):
        preference_facts = [item for item in preference_facts if item.key != fact.key]
        preference_facts.append(fact)
    preference_facts = preference_facts[-20:]
    objection = classify_primary_objection(user_message)
    objections = list(current.objections)
    if objection and (not objections or objections[-1] != objection):
        objections.append(objection)
    support_intent = classify_support_intent(resolved_message)
    sales_stage = classify_sales_stage(
        message=resolved_message,
        intent=support_intent,
        memory=replace(current, preference_facts=tuple(preference_facts)),
        objection=objection,
        handoff=response_kind is ResponseKind.HANDOFF,
    )
    question_ledger = list(current.question_ledger)
    existing_question_keys = {item.key for item in question_ledger}
    for item in extract_question_ledger_items(
        assistant_message,
        asked_revision=current.revision + 1,
    ):
        if item.key not in existing_question_keys:
            question_ledger.append(item)
            existing_question_keys.add(item.key)
    phrases = (*current.recent_response_phrases, *recent_response_phrases(assistant_message))
    interaction_preferences = merge_interaction_preferences(
        current.interaction_preferences,
        extract_interaction_preferences(user_message),
    )
    primary_goal = current.primary_goal
    if primary_goal is None and support_intent.value in {
        "product_recommendation",
        "product_comparison",
        "product_price",
        "product_material",
        "product_dimensions",
        "product_weight",
    }:
        primary_goal = support_intent.value
    next_best_action = {
        "human_handoff": "handoff",
        "discovery": "ask_one_question",
        "recommendation": "recommend",
        "comparison": "compare",
        "objection_handling": "address_objection",
        "purchase_ready": "offer_purchase_path",
    }.get(sales_stage.value, "answer")
    return ConversationWorkingMemory(
        active_product_sku=active_product_sku,
        pending_product_sku=pending_product_sku,
        candidate_product_skus=tuple(candidates),
        candidate_product_labels=candidate_labels,
        candidate_products=structured_candidates,
        country_code=country_code,
        currency=currency,
        confirmed_fields=tuple(sorted(confirmed_fields)),
        missing_fields=tuple(dict.fromkeys(answer_missing_fields))[:10],
        human_requested=current.human_requested
        or any(term in user_message.casefold() for term in _HUMAN_TERMS),
        last_intent=intent,
        response_language=response_language,
        unresolved_question=unresolved_question,
        primary_goal=primary_goal,
        preference_facts=tuple(preference_facts),
        objections=tuple(objections[-8:]),
        sales_stage=sales_stage.value,
        next_best_action=next_best_action,
        question_ledger=tuple(question_ledger[-20:]),
        recent_response_phrases=tuple(phrases[-12:]),
        interaction_preferences=interaction_preferences,
        revision=current.revision + 1,
    )


def select_relevant_memories(
    memories: tuple[DurableCustomerMemory, ...],
    *,
    message: str,
    limit: int = 5,
) -> tuple[DurableCustomerMemory, ...]:
    normalized = message.casefold()
    transactional = any(
        term in normalized
        for term in (
            "order status",
            "tracking",
            "refund",
            "payment",
            "cancel order",
            "订单状态",
            "物流单号",
            "退款",
            "支付",
            "取消订单",
        )
    )
    if transactional:
        return ()
    care = any(term in normalized for term in ("clean", "care", "store", "清洁", "保养", "收纳"))
    recommendation = any(
        term in normalized
        for term in ("recommend", "suitable", "looking for", "推荐", "适合", "我想找")
    )
    allowed = (
        {"verified_product", "troubleshooting", "resolution"}
        if care
        else {"preference", "verified_product"}
        if recommendation
        else {"preference", "verified_product", "troubleshooting", "resolution"}
    )
    query_tokens = set(re.findall(r"[a-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", normalized))
    ranked = []
    for position, memory in enumerate(memories):
        if memory.kind not in allowed:
            continue
        memory_tokens = set(
            re.findall(r"[a-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", memory.content.casefold())
        )
        overlap = len(query_tokens & memory_tokens)
        kind_boost = 2 if (care and memory.kind == "troubleshooting") else 1
        ranked.append((overlap + kind_boost, -position, memory))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return tuple(item[2] for item in ranked[: max(1, min(limit, 5))])


def extend_conversation_summary(
    current: ConversationSummary,
    turns: Iterable[ConversationTurn],
) -> ConversationSummary:
    turn_list = tuple(turns)
    skus = list(current.discussed_product_skus)
    intents = list(current.recent_intents)
    constraints = list(current.user_constraints)
    for turn in turn_list:
        if turn.role != "user":
            continue
        for sku in extract_product_skus(turn.content):
            if sku not in skus:
                skus.append(sku)
        intent = classify_conversation_intent(turn.content)
        if intent and (not intents or intents[-1] != intent):
            intents.append(intent)
        for constraint in _constraints_from_message(turn.content):
            if constraint not in constraints:
                constraints.append(constraint)
    skus = skus[-12:]
    intents = intents[-8:]
    constraints = constraints[-10:]
    sections = []
    if skus:
        sections.append(f"Products discussed: {', '.join(skus)}")
    if intents:
        sections.append(f"Recent intents: {', '.join(intents)}")
    if constraints:
        sections.append(f"User constraints: {'; '.join(constraints)}")
    return ConversationSummary(
        discussed_product_skus=tuple(skus),
        recent_intents=tuple(intents),
        user_constraints=tuple(constraints),
        compact_text="; ".join(sections)[:1000],
        summarized_message_count=current.summarized_message_count + len(turn_list),
    )


def _constraints_from_message(message: str) -> tuple[str, ...]:
    normalized = " ".join(message.casefold().split())
    constraints: list[str] = []
    country = _country_from_message(message)
    currency = _currency_from_message(message, country)
    if country:
        constraints.append(f"country={country}")
    if currency:
        constraints.append(f"currency={currency}")
    for material in ("tpe", "silicone", "pvc"):
        if material in normalized:
            constraints.append(f"material={material.upper()}")
    if any(term in normalized for term in ("lightweight", "easy to handle", "轻便", "容易搬")):
        constraints.append("handling=lightweight")
    budget = re.search(
        r"(?i)(?:under|below|budget(?: of)?|不超过|预算)\s*([$€£¥]?\s*\d+(?:\.\d{1,2})?)",
        message,
    )
    if budget:
        constraints.append(f"budget_max={budget.group(1).replace(' ', '')}")
    return tuple(constraints)


def classify_conversation_intent(message: str) -> str:
    normalized = message.casefold()
    support_intent = classify_support_intent(message)
    if support_intent.value != "general":
        return support_intent.value
    if classify_care_risk(message) is not None:
        return "product_care"
    if any(term in normalized for term in _MEANING_REFERENCES):
        return "reference_explanation"
    link_intent = classify_link_display_intent(message).value
    if link_intent != "none":
        return link_intent
    if extract_product_skus(message):
        return "product_lookup"
    return "general_question"


def _pending_product_clarification(sku: str, language: str) -> str:
    base = language.replace("_", "-").casefold().split("-", 1)[0]
    if base == "zh":
        return (
            f"您是想问商品代码 {sku} 的含义吗？我暂时无法在当前商品目录中匹配到这个型号。"
            "请检查代码是否正确，或发送对应的商品链接。"
        )
    return (
        f"Do you mean what the product code {sku} represents? I still can't match it "
        "to a current product. Please check the code or send the product link."
    )


def _active_product_clarification(sku: str, language: str) -> str:
    base = language.replace("_", "-").casefold().split("-", 1)[0]
    if base == "zh":
        return f"您是想了解型号 {sku} 的含义，还是页面上的某个词语？请把需要解释的具体文字发给我。"
    return (
        f"Do you mean the model code {sku}, or a specific word on the page? "
        "Please send the exact text you want explained."
    )


def _missing_reference_clarification(language: str) -> str:
    base = language.replace("_", "-").casefold().split("-", 1)[0]
    if base == "zh":
        return "您指的是哪个商品或哪段文字？请发送型号、商品链接或需要解释的原文。"
    return "Which product or text do you mean? Please send the SKU, product link, or exact phrase."


def _missing_comparison_reference_clarification(language: str) -> str:
    base = language.replace("_", "-").casefold().split("-", 1)[0]
    messages = {
        "zh": (
            "当前还没有可可靠比较的候选商品。请发送想比较的商品链接或型号，"
            "我会按已确认的价格和重量继续筛选。"
        ),
        "de": (
            "Derzeit gibt es noch keine zuverlässig vergleichbaren Kandidaten. Bitte senden "
            "Sie die Produktlinks oder Modellnummern."
        ),
        "en": (
            "There are no reliably comparable candidates yet. Please send the product links "
            "or model numbers, and I'll compare confirmed prices and weights."
        ),
    }
    return messages.get(base, messages["en"])


def _candidate_product_clarification(
    candidates: tuple[str, ...],
    language: str,
    labels: tuple[str, ...] = (),
) -> str:
    options = ", ".join(labels[:4] or candidates[:5])
    base = language.replace("_", "-").casefold().split("-", 1)[0]
    if base == "zh":
        return f"您指的是哪一款：{options}？请告诉我名称、价格或型号。"
    return f"Which one do you mean: {options}? Please tell me the name, price, or model number."


def _recommended_product_label(product: RecommendedProduct) -> str:
    price = ""
    if product.price:
        price = f" ({product.currency + ' ' if product.currency else ''}{product.price})"
    return f"{product.name}{price}"[:120]


def _country_from_message(message: str) -> str | None:
    normalized = message.casefold()
    return next(
        (
            country_code
            for country_code, terms in _COUNTRY_TERMS.items()
            if any(term in normalized for term in terms)
        ),
        None,
    )


def _currency_from_message(message: str, country_code: str | None) -> str | None:
    normalized = message.casefold()
    explicit = {
        "USD": ("usd", "us dollar", "美元"),
        "EUR": ("eur", "euro", "欧元"),
        "GBP": ("gbp", "pound", "英镑"),
        "CAD": ("cad", "canadian dollar", "加元"),
        "AUD": ("aud", "australian dollar", "澳元"),
        "JPY": ("jpy", "yen", "日元"),
        "CNY": ("cny", "rmb", "人民币"),
    }
    for currency, terms in explicit.items():
        if any(term in normalized for term in terms):
            return currency
    return _COUNTRY_CURRENCIES.get(country_code or "")


def _is_short_entity_reference(message: str) -> bool:
    return len(message.split()) <= 4 and any(term == message for term in _ENTITY_REFERENCES)


def _resolve_candidate_reference(
    message: str,
    memory: ConversationWorkingMemory,
) -> str | None:
    candidates = memory.candidate_products
    candidate_skus = memory.candidate_product_skus
    for terms, index in _ORDINAL_REFERENCES:
        if any(term in message for term in terms) and index < len(candidate_skus):
            return candidate_skus[index]
    if not candidates:
        return None
    if any(term in message for term in _CHEAPER_REFERENCES):
        return _relative_candidate(candidates, memory.active_product_sku, attribute="price") or (
            memory.active_product_sku
        )
    if any(term in message for term in _LIGHTER_REFERENCES):
        return _relative_candidate(candidates, memory.active_product_sku, attribute="weight") or (
            memory.active_product_sku
        )
    return None


def _relative_candidate(
    candidates: tuple[RecommendedProduct, ...],
    active_sku: str | None,
    *,
    attribute: str,
) -> str | None:
    current = next((item for item in candidates if item.sku == active_sku), None)
    current_value = _numeric_product_value(current, attribute) if current else None
    ranked = sorted(
        (
            (value, position, item.sku)
            for position, item in enumerate(candidates)
            if (value := _numeric_product_value(item, attribute)) is not None
            and item.sku != active_sku
            and (current_value is None or value < current_value)
        ),
        key=lambda item: (item[0], item[1]),
    )
    return ranked[0][2] if ranked else None


def _numeric_product_value(product: RecommendedProduct, attribute: str) -> Decimal | None:
    value = product.price if attribute == "price" else product.weight
    if not value:
        return None
    match = re.search(r"\d+(?:\.\d+)?", value.replace(",", ""))
    if match is None:
        return None
    try:
        numeric = Decimal(match.group(0))
    except InvalidOperation:
        return None
    if attribute == "weight" and ("lb" in value.casefold() or "磅" in value):
        return numeric * Decimal("0.45359237")
    return numeric
