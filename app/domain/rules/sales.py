import re
from collections.abc import Sequence
from dataclasses import replace
from urllib.parse import urlparse

from app.domain.models.answer import AnswerNextAction, AnswerPlan, SupportIntent
from app.domain.models.chat import ConversationTurn
from app.domain.models.memory import ConversationWorkingMemory
from app.domain.models.principal import AuthenticatedPrincipal
from app.domain.models.sales import (
    ConversationLanguageContext,
    MemoryFactStatus,
    QuestionLedgerItem,
    SalesMemoryFact,
    SalesNextAction,
    SalesResponsePlan,
    SalesStage,
    SiteIdentityProfile,
)
from app.domain.rules.conversation import (
    detect_message_language,
    normalize_language_tag,
)
from app.domain.rules.playbooks import select_conversation_playbook

_EXPLICIT_LANGUAGES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("zh", ("用中文", "中文回复", "speak chinese", "in chinese")),
    ("en", ("用英语", "英文回复", "speak english", "in english")),
    ("de", ("用德语", "德语回复", "auf deutsch", "in german")),
    ("fr", ("用法语", "法语回复", "en français", "in french")),
    ("es", ("用西班牙语", "西班牙语回复", "en español", "in spanish")),
    ("ja", ("用日语", "日语回复", "日本語で", "in japanese")),
)
_PURCHASE_READY_TERMS = (
    "buy it",
    "place the order",
    "checkout",
    "purchase link",
    "立即购买",
    "准备下单",
    "购买链接",
    "jetzt kaufen",
    "bestellen",
)
_OBJECTION_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("price", ("too expensive", "price is high", "太贵", "价格高", "zu teuer")),
    ("privacy", ("privacy", "discreet", "私密", "隐私", "diskret")),
    ("trust", ("scam", "trust", "legit", "骗局", "可信", "betrug", "seriös")),
    ("delivery", ("too long", "delivery worry", "配送太久", "到货太慢", "lieferzeit")),
    ("maintenance", ("hard to clean", "maintenance", "难清洁", "保养麻烦", "pflege")),
)
_QUESTION_KEY_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("budget", ("budget", "price range", "预算", "预算范围", "preisrahmen")),
    ("country", ("country", "deliver to", "国家", "配送到", "land")),
    ("material", ("material", "tpe", "silicone", "材质", "材料", "硅胶")),
    ("size", ("size", "height", "尺寸", "身高", "größe")),
    (
        "weight",
        ("weight", "lightweight", "重量", "轻便", "轻一点", "轻量", "gewicht"),
    ),
)
_PROHIBITED_CLAIMS = (
    "invented_discount",
    "fake_urgency",
    "absolute_safety_guarantee",
    "absolute_legal_guarantee",
    "unsupported_delivery_promise",
    "unsupported_popularity_claim",
)
_INTERACTION_PREFERENCE_TERMS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "response_length",
        "concise",
        (
            "keep it short",
            "keep later answers short",
            "keep all later answers short",
            "keep future answers short",
            "be brief",
            "short answer",
            "简短一点",
            "简单说",
            "直接简短",
        ),
    ),
    (
        "response_length",
        "detailed",
        ("explain in detail", "detailed answer", "详细说明", "展开讲讲", "讲详细一点"),
    ),
    (
        "answer_order",
        "direct",
        (
            "just answer",
            "give me the answer",
            "直接回答",
            "直接简短回答",
            "直接说结论",
            "先说结论",
        ),
    ),
    (
        "sales_tone",
        "no_sales",
        (
            "don't sell",
            "do not sell",
            "don't recommend",
            "do not recommend",
            "no sales pitch",
            "不要推销",
            "别推销",
            "不用推荐其他",
        ),
    ),
)

_CUSTOMER_QUESTION_SIGNAL = re.compile(
    r"(?i)[?？]|\b(?:what|which|who|where|when|why|how|is|are|can|could|will|would|"
    r"does|tell me|show me|explain)\b|(?:什么|哪(?:个|款|里)?|谁|何时|为什么|怎么|如何|"
    r"是否|能否|可以|告诉我|说明|解释)"
)


def resolve_site_identity(principal: AuthenticatedPrincipal) -> SiteIdentityProfile:
    domain = ""
    if principal.site_domain:
        parsed = urlparse(principal.site_domain)
        domain = parsed.netloc.casefold() if parsed.netloc else principal.site_domain.casefold()
    return SiteIdentityProfile(
        domain=domain,
        agent_display_name=principal.agent_display_name or "",
        agent_identity_type=principal.agent_identity_type,
        customer_address_mode=principal.customer_address_mode,
        introduce_on_first_turn=principal.introduce_on_first_turn,
        profile_version=principal.site_identity_version,
    )


def resolve_conversation_language_context(
    *,
    message: str,
    history: tuple[ConversationTurn, ...],
    preferred_language: str | None,
    previous_language: str | None,
    address_mode: str,
    channel: str = "widget",
) -> ConversationLanguageContext:
    explicit = _explicit_language(message)
    current = detect_message_language(message) if _has_substantive_language(message) else None
    stable_history = _stable_history_language(history)
    normalized_previous = normalize_language_tag(previous_language) if previous_language else None
    if explicit:
        target, source, confidence = explicit, "customer_explicit", 1.0
    elif current:
        target, source, confidence = current, "current_message", 0.95
    elif normalized_previous:
        target, source, confidence = normalized_previous, "conversation_memory", 0.85
    elif stable_history:
        target, source, confidence = stable_history, "recent_history", 0.8
    else:
        target, source, confidence = normalize_language_tag(preferred_language), "site_default", 0.5
    previous_base = _language_base(normalized_previous or stable_history)
    target_base = _language_base(target)
    return ConversationLanguageContext(
        target_language=target,
        language_confidence=confidence,
        language_source=source,
        previous_language=normalized_previous or stable_history,
        language_switched=bool(previous_base and target_base != previous_base),
        address_form=address_mode
        if address_mode in {"formal", "neutral", "friendly"}
        else "neutral",
        formality_level=(
            address_mode if address_mode in {"formal", "neutral", "friendly"} else "neutral"
        ),
        channel=channel,
        locale_hint=normalize_language_tag(preferred_language) if preferred_language else None,
        response_length=_response_length(message),
    )


def build_sales_response_plan(
    *,
    message: str,
    answer_plan: AnswerPlan,
    memory: ConversationWorkingMemory,
    language_context: ConversationLanguageContext,
    site_identity: SiteIdentityProfile,
) -> SalesResponsePlan:
    objection = classify_primary_objection(message) or (
        memory.objections[-1] if memory.objections else None
    )
    stage = classify_sales_stage(
        message=message,
        intent=answer_plan.intent,
        memory=memory,
        objection=objection,
        handoff=answer_plan.next_action is AnswerNextAction.HANDOFF,
    )
    follow_up_key = _follow_up_question_key(answer_plan, memory)
    next_action = _next_action(stage, answer_plan, follow_up_key)
    confirmed_preferences = tuple(
        f"{item.key}={item.value}"
        for item in memory.preference_facts
        if item.status is MemoryFactStatus.CONFIRMED
    )
    playbook = select_conversation_playbook(
        intent=answer_plan.intent,
        objection=objection,
        handoff=answer_plan.next_action is AnswerNextAction.HANDOFF,
    )
    return SalesResponsePlan(
        current_intent=answer_plan.intent.value,
        sales_stage=stage,
        customer_primary_goal=memory.primary_goal,
        confirmed_preferences=confirmed_preferences,
        primary_objection=objection,
        direct_answer_required=True,
        evidence_required=tuple(fact.fact_type for fact in answer_plan.confirmed_facts[:20]),
        recommended_product_ids=answer_plan.product_ids[:3],
        recommendation_reasons=_recommendation_reasons(memory, answer_plan.product_ids),
        response_moves=_response_moves(stage, objection, follow_up_key),
        missing_information=answer_plan.missing_fields[:5],
        follow_up_question_key=follow_up_key,
        next_best_action=next_action,
        target_language=language_context.target_language,
        address_form=language_context.address_form,
        formality_level=language_context.formality_level,
        max_words=answer_plan.max_words,
        should_introduce=(
            memory.revision == 0
            and site_identity.introduce_on_first_turn
            and bool(site_identity.agent_display_name)
        ),
        should_acknowledge_emotion=objection is not None,
        recent_phrases_to_avoid=memory.recent_response_phrases[-8:],
        prohibited_claims=tuple(dict.fromkeys((*_PROHIBITED_CLAIMS, *playbook.prohibited_claims))),
        handoff_required=answer_plan.next_action is AnswerNextAction.HANDOFF,
        playbook_id=playbook.card_id,
        playbook_evidence_slots=playbook.evidence_slots,
    )


def _recommendation_reasons(
    memory: ConversationWorkingMemory,
    product_ids: tuple[str, ...],
) -> tuple[str, ...]:
    selected = set(product_ids[:3])
    reasons: list[str] = []
    for product in memory.candidate_products:
        if selected and product.sku not in selected:
            continue
        sections = []
        if product.match_reasons:
            sections.append(f"match={','.join(product.match_reasons)}")
        if product.tradeoffs:
            sections.append(f"tradeoff={','.join(product.tradeoffs)}")
        if product.missing_facts:
            sections.append(f"missing={','.join(product.missing_facts)}")
        if sections:
            reasons.append(f"{product.sku}:{';'.join(sections)}")
    return tuple(reasons[:3])


def classify_sales_stage(
    *,
    message: str,
    intent: SupportIntent,
    memory: ConversationWorkingMemory,
    objection: str | None = None,
    handoff: bool = False,
) -> SalesStage:
    normalized = " ".join(message.casefold().split())
    if handoff or intent is SupportIntent.HUMAN_HANDOFF:
        return SalesStage.HUMAN_HANDOFF
    if any(term in normalized for term in _PURCHASE_READY_TERMS):
        return SalesStage.PURCHASE_READY
    if objection:
        return SalesStage.OBJECTION_HANDLING
    if intent is SupportIntent.PRODUCT_COMPARISON:
        return SalesStage.COMPARISON
    if intent is SupportIntent.PRODUCT_RECOMMENDATION:
        return SalesStage.RECOMMENDATION if memory.preference_facts else SalesStage.DISCOVERY
    if memory.candidate_product_skus and intent in {
        SupportIntent.PRODUCT_PRICE,
        SupportIntent.PRODUCT_STOCK,
        SupportIntent.PRODUCT_MATERIAL,
        SupportIntent.PRODUCT_DIMENSIONS,
        SupportIntent.PRODUCT_WEIGHT,
        SupportIntent.DELIVERY_ESTIMATE,
    }:
        return SalesStage.DECISION_SUPPORT
    if memory.revision == 0:
        return SalesStage.FIRST_CONTACT
    if intent is SupportIntent.GENERAL and not memory.primary_goal:
        return SalesStage.DISCOVERY
    return SalesStage.QUALIFICATION


def extract_sales_memory_facts(
    message: str, *, source_revision: int
) -> tuple[SalesMemoryFact, ...]:
    normalized = " ".join(message.casefold().split())
    facts: list[SalesMemoryFact] = []
    material_terms = {
        "TPE": ("tpe", "thermoplastic elastomer", "热塑性弹性体"),
        "SILICONE": ("silicone", "silikon", "silicona", "silicone", "硅胶"),
        "PVC": ("pvc", "polyvinyl chloride", "聚氯乙烯"),
    }
    excluded_materials = _excluded_values(normalized, material_terms)
    for material, terms in material_terms.items():
        if any(term in normalized for term in terms):
            if material in excluded_materials:
                facts.append(_fact("excluded_material", material, source_revision))
            else:
                facts.append(_fact("material", material, source_revision))
    if any(
        term in normalized
        for term in (
            "lightweight",
            "light weight",
            "easy to handle",
            "easy to move",
            "leichter",
            "leicht zu bewegen",
            "ligero",
            "ligera",
            "facile à déplacer",
            "轻便",
            "轻量",
            "轻一点",
            "重量轻",
            "容易搬",
            "方便搬",
            "方便移动",
        )
    ):
        facts.append(_fact("handling", "lightweight", source_revision))
    if any(
        term in normalized
        for term in (
            "easy to store",
            "compact storage",
            "small storage space",
            "leicht zu verstauen",
            "fácil de guardar",
            "facile à ranger",
            "容易收纳",
            "方便收纳",
            "收纳方便",
            "储存空间小",
        )
    ):
        facts.append(_fact("storage", "compact", source_revision))
    budget = _budget_match(message)
    if budget:
        amount = budget.group("amount").replace(",", "")
        prefix = budget.group("prefix") or ""
        currency = _currency_from_budget_match(budget)
        facts.append(_fact("budget_max", f"{prefix}{amount}", source_revision))
        if currency:
            facts.append(_fact("budget_currency", currency, source_revision))
    size = re.search(r"(?i)(\d{2,3})\s*(?:cm|厘米)", message)
    if size:
        facts.append(_fact("preferred_height_cm", size.group(1), source_revision))
    max_weight = re.search(
        r"(?i)(?:under|below|no more than|maximum|max\.?|不超过|最多|低于)\s*"
        r"(?P<amount>\d+(?:\.\d+)?)\s*(?P<unit>kg|公斤|千克|lb|lbs|磅)\b",
        message,
    )
    if max_weight:
        facts.append(
            _fact(
                "max_weight",
                f"{max_weight.group('amount')} {max_weight.group('unit')}",
                source_revision,
            )
        )
    feature_terms = {
        "standing": ("standing feet", "standing", "stehfunktion", "站立", "站立脚"),
        "heating": ("heating", "heated", "heizung", "加热", "温控"),
        "removable_parts": (
            "removable",
            "detachable",
            "abnehmbar",
            "可拆卸",
            "可替换",
        ),
        "oral_function": ("oral", "mouth", "口腔", "口交"),
    }
    excluded_features = _excluded_values(normalized, feature_terms)
    for feature, terms in feature_terms.items():
        if any(term in normalized for term in terms):
            key = "excluded_feature" if feature in excluded_features else "feature"
            facts.append(_fact(key, feature, source_revision))
    return tuple(facts)


def merge_current_turn_sales_memory(
    memory: ConversationWorkingMemory,
    *,
    message: str,
) -> ConversationWorkingMemory:
    """Build an in-memory view for planning without advancing the persisted revision."""
    facts = list(memory.preference_facts)
    for fact in extract_sales_memory_facts(message, source_revision=memory.revision + 1):
        if fact.key in {"feature", "excluded_feature", "excluded_material"}:
            facts = [
                item for item in facts if not (item.key == fact.key and item.value == fact.value)
            ]
        else:
            facts = [item for item in facts if item.key != fact.key]
        facts.append(fact)
    objection = classify_primary_objection(message)
    objections = list(memory.objections)
    if objection and (not objections or objections[-1] != objection):
        objections.append(objection)
    interaction_preferences = merge_interaction_preferences(
        memory.interaction_preferences,
        extract_interaction_preferences(message),
    )
    return replace(
        memory,
        preference_facts=tuple(facts[-30:]),
        objections=tuple(objections[-8:]),
        interaction_preferences=interaction_preferences,
    )


def extract_interaction_preferences(message: str) -> tuple[str, ...]:
    normalized = " ".join(message.casefold().split())
    return tuple(
        f"{key}={value}"
        for key, value, terms in _INTERACTION_PREFERENCE_TERMS
        if any(term in normalized for term in terms)
    )


def interaction_preference_acknowledgement(message: str, language: str) -> str | None:
    preferences = extract_interaction_preferences(message)
    if not preferences or _CUSTOMER_QUESTION_SIGNAL.search(message):
        return None
    if language.casefold().startswith("zh"):
        return "明白了，后续回复我会按这些偏好来，不额外推销或追问。"
    return "Understood. I'll follow those response preferences in later replies."


def merge_interaction_preferences(
    current: tuple[str, ...],
    updates: tuple[str, ...],
) -> tuple[str, ...]:
    values = {item.partition("=")[0]: item for item in current if "=" in item}
    for item in updates:
        key, separator, _value = item.partition("=")
        if separator:
            values[key] = item
    return tuple(values[key] for key in sorted(values))[:10]


def classify_primary_objection(message: str) -> str | None:
    normalized = " ".join(message.casefold().split())
    return next(
        (kind for kind, terms in _OBJECTION_TERMS if any(term in normalized for term in terms)),
        None,
    )


def question_key(text: str) -> str:
    normalized = " ".join(text.casefold().split())
    for key, terms in _QUESTION_KEY_TERMS:
        if any(term in normalized for term in terms):
            return key
    words = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", normalized)
    return "-".join(words[:8])[:80] or "question"


def extract_question_ledger_items(
    message: str,
    *,
    asked_revision: int,
) -> tuple[QuestionLedgerItem, ...]:
    clauses = re.findall(r"[^?？\n]{2,240}[?？]", message)
    return tuple(
        QuestionLedgerItem(
            key=question_key(clause),
            text=" ".join(clause.split())[:240],
            status="asked",
            asked_revision=asked_revision,
        )
        for clause in clauses[-3:]
    )


def recent_response_phrases(message: str) -> tuple[str, ...]:
    sentences = re.split(r"(?<=[.!?。！？])\s+|\n+", message.strip())
    return tuple(sentence.strip()[:160] for sentence in sentences if sentence.strip())[:3]


def _fact(key: str, value: str, revision: int) -> SalesMemoryFact:
    return SalesMemoryFact(key, value, MemoryFactStatus.CONFIRMED, revision)


def _budget_match(message: str) -> re.Match[str] | None:
    return re.search(
        r"(?ix)(?:"
        r"budget(?:\s+(?:is|of|around|about|approximately))?\s*(?:is|=|:)?|"
        r"under|below|less\s+than|up\s+to|"
        r"预算(?:大约|大概|约|是|为|在)?\s*(?:是|为|:|：)?|"
        r"不超过|低于|最多|以内|"
        r"budget\s+liegt\s+bei|unter|"
        r"presupuesto(?:\s+es|\s+de)?|"
        r"budget(?:\s+est|\s+de)?"
        r")\s*"
        r"(?P<prefix>[$€£¥￥])?\s*"
        r"(?P<amount>\d{2,6}(?:,\d{3})*(?:\.\d{1,2})?)\s*"
        r"(?P<suffix>usd|eur|gbp|cny|rmb|dollars?|euros?|美元|美金|欧元|英镑|人民币)?",
        message,
    )


def _currency_from_budget_match(match: re.Match[str]) -> str | None:
    value = f"{match.group('prefix') or ''} {match.group('suffix') or ''}".casefold()
    if "$" in value or "usd" in value or "dollar" in value or "美元" in value or "美金" in value:
        return "USD"
    if "€" in value or "eur" in value or "euro" in value or "欧元" in value:
        return "EUR"
    if "£" in value or "gbp" in value or "英镑" in value:
        return "GBP"
    if "¥" in value or "￥" in value or "cny" in value or "rmb" in value or "人民币" in value:
        return "CNY"
    return None


def _excluded_values(
    normalized: str,
    values: dict[str, tuple[str, ...]],
) -> set[str]:
    excluded: set[str] = set()
    negative_prefixes = (
        "not ",
        "no ",
        "without ",
        "don't want ",
        "do not want ",
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
    for value, terms in values.items():
        if any(f"{prefix}{term}" in normalized for prefix in negative_prefixes for term in terms):
            excluded.add(value)
    return excluded


def _explicit_language(message: str) -> str | None:
    normalized = " ".join(message.casefold().split())
    return next(
        (
            language
            for language, terms in _EXPLICIT_LANGUAGES
            if any(term in normalized for term in terms)
        ),
        None,
    )


def _has_substantive_language(message: str) -> bool:
    without_urls = re.sub(r"https?://\S+", " ", message)
    without_codes = re.sub(r"\b[A-Z0-9_-]{4,}\b", " ", without_urls)
    letters = re.findall(r"[A-Za-z\u4e00-\u9fff\u3040-\u30ff\u0400-\u04ff]", without_codes)
    return len(letters) >= 2


def _stable_history_language(history: Sequence[ConversationTurn]) -> str | None:
    detected = [
        language
        for turn in history[-6:]
        if turn.role == "user" and _has_substantive_language(turn.content)
        if (language := detect_message_language(turn.content)) is not None
    ]
    if not detected:
        return None
    recent = detected[-3:]
    return max(dict.fromkeys(recent), key=recent.count)


def _response_length(message: str) -> str:
    question_count = message.count("?") + message.count("？")
    if len(message) > 500 or question_count > 2:
        return "detailed"
    if len(message) > 180 or question_count > 1:
        return "medium"
    return "short"


def _language_base(language: str | None) -> str:
    return (language or "").replace("_", "-").casefold().split("-", 1)[0]


def _follow_up_question_key(
    answer_plan: AnswerPlan,
    memory: ConversationWorkingMemory,
) -> str | None:
    candidates = list(answer_plan.missing_fields)
    if answer_plan.intent is SupportIntent.PRODUCT_RECOMMENDATION and not memory.preference_facts:
        candidates.append("budget")
    if answer_plan.intent is SupportIntent.SHIPPING_COVERAGE and not memory.country_code:
        candidates.append("country")
    asked = {item.key for item in memory.question_ledger}
    return next((key for key in candidates if key not in asked), None)


def _next_action(
    stage: SalesStage,
    answer_plan: AnswerPlan,
    follow_up_key: str | None,
) -> SalesNextAction:
    if stage is SalesStage.HUMAN_HANDOFF:
        return SalesNextAction.HANDOFF
    if follow_up_key:
        return SalesNextAction.ASK_ONE_QUESTION
    if stage is SalesStage.COMPARISON:
        return SalesNextAction.COMPARE
    if stage is SalesStage.RECOMMENDATION:
        return SalesNextAction.RECOMMEND
    if stage is SalesStage.OBJECTION_HANDLING:
        return SalesNextAction.ADDRESS_OBJECTION
    if stage is SalesStage.PURCHASE_READY:
        return SalesNextAction.OFFER_PURCHASE_PATH
    return SalesNextAction.ANSWER


def _response_moves(
    stage: SalesStage,
    objection: str | None,
    follow_up_key: str | None,
) -> tuple[str, ...]:
    moves = ["answer_current_question_first", "reuse_confirmed_context_without_repeating_it"]
    if objection == "trust":
        moves.extend(("acknowledge_specific_concern", "offer_only_verifiable_trust_evidence"))
    elif objection == "privacy":
        moves.extend(
            ("acknowledge_privacy_concern", "explain_only_evidence_backed_privacy_measures")
        )
    elif objection == "price":
        moves.extend(("state_verified_price_or_limit", "connect_value_to_customer_priorities"))
    elif objection == "delivery":
        moves.extend(("state_published_delivery_range", "avoid_arrival_guarantee"))
    elif objection == "maintenance":
        moves.extend(("give_safe_practical_guidance", "avoid_one_method_fits_all_claim"))
    elif stage is SalesStage.COMPARISON:
        moves.extend(
            ("compare_only_customer_relevant_attributes", "identify_best_fit_without_pressure")
        )
    elif stage is SalesStage.RECOMMENDATION:
        moves.extend(("recommend_up_to_three_options", "explain_each_fit_with_objective_facts"))
    elif stage is SalesStage.PURCHASE_READY:
        moves.append("offer_verified_purchase_path_without_urgency")
    if follow_up_key:
        moves.append(f"ask_one_follow_up:{follow_up_key}")
    return tuple(moves)
