from dataclasses import dataclass
from enum import StrEnum


class SupportIntent(StrEnum):
    GENERAL = "general"
    HUMAN_HANDOFF = "human_handoff"
    PRODUCT_RECOMMENDATION = "product_recommendation"
    PRODUCT_COMPARISON = "product_comparison"
    PRODUCT_PRICE = "product_price"
    PRODUCT_STOCK = "product_stock"
    PRODUCT_MATERIAL = "product_material"
    PRODUCT_DIMENSIONS = "product_dimensions"
    PRODUCT_WEIGHT = "product_weight"
    PRODUCT_CARE = "product_care"
    DELIVERY_ESTIMATE = "delivery_estimate"
    SHIPPING_COVERAGE = "shipping_coverage"
    DISCOUNT = "discount"
    RETURN_POLICY = "return_policy"
    WARRANTY = "warranty"
    PAYMENT_METHODS = "payment_methods"
    ORDER_STATUS = "order_status"
    ORDER_TRACKING = "order_tracking"
    ORDER_CANCELLATION = "order_cancellation"
    REFUND = "refund"
    LEGAL_SALES = "legal_sales"
    PRIVACY_PACKAGING = "privacy_packaging"
    SITE_TRUST = "site_trust"
    PRODUCT_CUSTOMIZATION = "product_customization"
    SHIPPING_CUSTOMS = "shipping_customs"


class FactSourceMode(StrEnum):
    KNOWLEDGE = "knowledge"
    REALTIME = "realtime"
    REALTIME_OR_FRESH_KNOWLEDGE = "realtime_or_fresh_knowledge"
    MIXED = "mixed"


class AnswerNextAction(StrEnum):
    ANSWER = "answer"
    CLARIFY = "clarify"
    REALTIME_LOOKUP = "realtime_lookup"
    HANDOFF = "handoff"


@dataclass(frozen=True, slots=True)
class AnswerContract:
    intent: SupportIntent
    source_mode: FactSourceMode
    required_fact_types: tuple[str, ...]
    max_words: int
    response_style: str


@dataclass(frozen=True, slots=True)
class ConfirmedFact:
    fact_type: str
    value: str
    source: str


@dataclass(frozen=True, slots=True)
class AnswerPlan:
    intent: SupportIntent
    product_ids: tuple[str, ...]
    confirmed_facts: tuple[ConfirmedFact, ...]
    missing_fields: tuple[str, ...]
    claims: tuple[str, ...]
    next_action: AnswerNextAction
    source_mode: FactSourceMode
    max_words: int
    response_style: str


@dataclass(frozen=True, slots=True)
class RecommendedProduct:
    sku: str
    name: str
    price: str | None = None
    currency: str | None = None
    source_url: str | None = None
    material: str | None = None
    weight: str | None = None
    dimensions: dict[str, str] | None = None
    match_reasons: tuple[str, ...] = ()
    tradeoffs: tuple[str, ...] = ()
    missing_facts: tuple[str, ...] = ()
