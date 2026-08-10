import re

from app.domain.models.answer import (
    AnswerContract,
    AnswerNextAction,
    AnswerPlan,
    ConfirmedFact,
    FactSourceMode,
    SupportIntent,
)
from app.domain.rules.order_handoff import classify_order_handoff

_ORDER_REFERENCE = re.compile(r"(?i)\b(?:order|订单)[\s:#-]*[a-z0-9][a-z0-9_-]{3,}\b")

_INTENT_TERMS: tuple[tuple[SupportIntent, tuple[str, ...]], ...] = (
    (SupportIntent.HUMAN_HANDOFF, ("human agent", "real person", "人工客服", "转人工", "真人客服")),
    (SupportIntent.ORDER_CANCELLATION, ("cancel my order", "cancel order", "取消订单")),
    (
        SupportIntent.ORDER_TRACKING,
        ("tracking number", "track my order", "物流轨迹", "物流单号", "追踪订单"),
    ),
    (SupportIntent.ORDER_STATUS, ("order status", "where is my order", "订单状态", "订单到哪")),
    (SupportIntent.REFUND, ("refund", "退款", "退钱")),
    (SupportIntent.DISCOUNT, ("discount", "coupon", "promo code", "优惠", "折扣", "优惠码")),
    (
        SupportIntent.PRIVACY_PACKAGING,
        (
            "discreet packaging",
            "package discreet",
            "plain packaging",
            "privacy packaging",
            "package reveal what is inside",
            "package show what is inside",
            "show the product name on the package",
            "隐私包装",
            "私密包装",
            "包装会不会显示商品",
            "包裹会不会暴露内容",
        ),
    ),
    (
        SupportIntent.SITE_TRUST,
        ("is this site legit", "is this a scam", "can i trust", "网站靠谱吗", "是骗局吗", "可信吗"),
    ),
    (
        SupportIntent.PRODUCT_CUSTOMIZATION,
        ("customize", "custom order", "custom made", "定制", "客制化"),
    ),
    (
        SupportIntent.SHIPPING_CUSTOMS,
        ("customs duty", "import tax", "customs fee", "关税", "进口税", "海关费用"),
    ),
    (SupportIntent.PRODUCT_COMPARISON, ("compare", "difference between", "对比", "区别", "哪个好")),
    (
        SupportIntent.PRODUCT_RECOMMENDATION,
        (
            "recommend",
            "looking for",
            "suitable",
            "推荐",
            "适合",
            "我想买",
            "我想找",
        ),
    ),
    (SupportIntent.PRODUCT_PRICE, ("how much", "price", "cost", "多少钱", "价格", "售价")),
    (
        SupportIntent.PRODUCT_STOCK,
        (
            "in stock",
            "availability",
            "available in",
            "available now",
            "is it available",
            "is this available",
            "us stock",
            "usa stock",
            "库存",
            "现货",
            "有货",
        ),
    ),
    (
        SupportIntent.DELIVERY_ESTIMATE,
        (
            "estimated delivery",
            "delivery time",
            "shipping time",
            "how many days",
            "how long will it take",
            "多久送达",
            "几天送达",
            "配送时间",
            "到货时间",
        ),
    ),
    (
        SupportIntent.SHIPPING_COVERAGE,
        ("ship to", "deliver to", "shipping area", "配送到", "能寄到", "配送范围"),
    ),
    (SupportIntent.RETURN_POLICY, ("return policy", "return this", "退货", "退换货")),
    (SupportIntent.WARRANTY, ("warranty", "guarantee period", "保修", "质保")),
    (SupportIntent.PAYMENT_METHODS, ("payment method", "pay with", "付款方式", "支付方式")),
    (SupportIntent.PRODUCT_MATERIAL, ("material", "made of", "材质", "材料")),
    (SupportIntent.PRODUCT_DIMENSIONS, ("dimension", "height", "size", "尺寸", "高度", "大小")),
    (SupportIntent.PRODUCT_WEIGHT, ("weight", "weigh", "重量", "多重")),
    (SupportIntent.PRODUCT_CARE, ("clean", "care", "store it", "清洁", "保养", "收纳")),
    (SupportIntent.LEGAL_SALES, ("legal", "lawful", "合法", "法律")),
)

_CONTRACTS = {
    SupportIntent.PRODUCT_PRICE: AnswerContract(
        SupportIntent.PRODUCT_PRICE,
        FactSourceMode.REALTIME_OR_FRESH_KNOWLEDGE,
        ("price", "currency"),
        60,
        "answer_first",
    ),
    SupportIntent.PRODUCT_STOCK: AnswerContract(
        SupportIntent.PRODUCT_STOCK,
        FactSourceMode.REALTIME_OR_FRESH_KNOWLEDGE,
        ("stock_status",),
        60,
        "answer_first",
    ),
    SupportIntent.DISCOUNT: AnswerContract(
        SupportIntent.DISCOUNT,
        FactSourceMode.KNOWLEDGE,
        (),
        70,
        "answer_first",
    ),
    SupportIntent.ORDER_STATUS: AnswerContract(
        SupportIntent.ORDER_STATUS,
        FactSourceMode.REALTIME,
        ("order_status", "updated_at"),
        70,
        "task_status",
    ),
    SupportIntent.ORDER_TRACKING: AnswerContract(
        SupportIntent.ORDER_TRACKING,
        FactSourceMode.REALTIME,
        ("tracking_status", "last_event_at"),
        90,
        "task_status",
    ),
    SupportIntent.ORDER_CANCELLATION: AnswerContract(
        SupportIntent.ORDER_CANCELLATION,
        FactSourceMode.REALTIME,
        ("order_status", "cancellation_eligibility"),
        90,
        "action_request",
    ),
    SupportIntent.REFUND: AnswerContract(
        SupportIntent.REFUND,
        FactSourceMode.REALTIME,
        ("refund_status", "refund_eligibility"),
        100,
        "action_request",
    ),
    SupportIntent.DELIVERY_ESTIMATE: AnswerContract(
        SupportIntent.DELIVERY_ESTIMATE,
        FactSourceMode.MIXED,
        ("processing_time", "shipping_time", "region"),
        80,
        "answer_first",
    ),
    SupportIntent.SHIPPING_COVERAGE: AnswerContract(
        SupportIntent.SHIPPING_COVERAGE,
        FactSourceMode.MIXED,
        ("shipping_region",),
        70,
        "answer_first",
    ),
    SupportIntent.PRODUCT_COMPARISON: AnswerContract(
        SupportIntent.PRODUCT_COMPARISON,
        FactSourceMode.MIXED,
        ("comparable_attributes",),
        150,
        "comparison",
    ),
    SupportIntent.PRODUCT_RECOMMENDATION: AnswerContract(
        SupportIntent.PRODUCT_RECOMMENDATION,
        FactSourceMode.MIXED,
        ("product_attributes",),
        140,
        "recommendation",
    ),
    SupportIntent.HUMAN_HANDOFF: AnswerContract(
        SupportIntent.HUMAN_HANDOFF,
        FactSourceMode.KNOWLEDGE,
        (),
        40,
        "handoff",
    ),
}

_DEFAULT_CONTRACT = AnswerContract(
    SupportIntent.GENERAL,
    FactSourceMode.KNOWLEDGE,
    ("relevant_evidence",),
    120,
    "answer_first",
)


def classify_support_intent(message: str) -> SupportIntent:
    normalized = " ".join(message.casefold().split())
    if "product_reference:" in normalized and any(
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
    ):
        return SupportIntent.PRODUCT_RECOMMENDATION
    if any(
        term in normalized
        for term in ("refund policy", "return policy", "退款政策", "退货政策", "退换货政策")
    ):
        return SupportIntent.RETURN_POLICY
    for intent, terms in _INTENT_TERMS:
        if any(term in normalized for term in terms):
            return intent
    return SupportIntent.GENERAL


def answer_contract_for(intent: SupportIntent) -> AnswerContract:
    if intent in _CONTRACTS:
        return _CONTRACTS[intent]
    if intent in {
        SupportIntent.RETURN_POLICY,
        SupportIntent.WARRANTY,
        SupportIntent.PAYMENT_METHODS,
        SupportIntent.PRODUCT_MATERIAL,
        SupportIntent.PRODUCT_DIMENSIONS,
        SupportIntent.PRODUCT_WEIGHT,
        SupportIntent.PRODUCT_CARE,
        SupportIntent.LEGAL_SALES,
        SupportIntent.PRIVACY_PACKAGING,
        SupportIntent.SITE_TRUST,
        SupportIntent.PRODUCT_CUSTOMIZATION,
        SupportIntent.SHIPPING_CUSTOMS,
    }:
        return AnswerContract(
            intent,
            FactSourceMode.KNOWLEDGE,
            (intent.value,),
            100,
            "answer_first",
        )
    return _DEFAULT_CONTRACT


def build_answer_plan(
    *,
    message: str,
    product_ids: tuple[str, ...] = (),
    confirmed_facts: tuple[ConfirmedFact, ...] = (),
    claims: tuple[str, ...] = (),
) -> AnswerPlan:
    intent = classify_support_intent(message)
    contract = answer_contract_for(intent)
    order_handoff = classify_order_handoff(message)
    missing_fields: list[str] = []
    if intent in {
        SupportIntent.ORDER_STATUS,
        SupportIntent.ORDER_TRACKING,
        SupportIntent.ORDER_CANCELLATION,
        SupportIntent.REFUND,
    } and not _ORDER_REFERENCE.search(message):
        missing_fields.append("order_reference")
    if order_handoff is not None or intent is SupportIntent.HUMAN_HANDOFF:
        next_action = AnswerNextAction.HANDOFF
    elif missing_fields:
        next_action = AnswerNextAction.CLARIFY
    elif contract.source_mode is FactSourceMode.REALTIME:
        next_action = AnswerNextAction.REALTIME_LOOKUP
    else:
        next_action = AnswerNextAction.ANSWER
    return AnswerPlan(
        intent=intent,
        product_ids=tuple(dict.fromkeys(product_ids))[:5],
        confirmed_facts=confirmed_facts[:20],
        missing_fields=tuple(missing_fields),
        claims=claims[:20],
        next_action=next_action,
        source_mode=contract.source_mode,
        max_words=contract.max_words,
        response_style=contract.response_style,
    )
