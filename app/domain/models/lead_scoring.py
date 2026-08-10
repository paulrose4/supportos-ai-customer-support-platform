from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class LeadIntentTier(StrEnum):
    """Commercial-intent bands used by the operations queue."""

    NURTURE = "nurture"
    UNKNOWN = "unknown"
    WARM = "warm"
    HOT = "hot"

    # Compatibility aliases for the pre-release vocabulary. Commercial
    # intent deliberately has no urgency band; urgency belongs to operations.
    COLD = "nurture"
    URGENT = "hot"


class LeadOperationPriority(StrEnum):
    """Operational urgency is deliberately separate from commercial intent."""

    P0 = "P0"
    P1 = "P1"
    P2 = "P2"

    # Compatibility aliases for callers that used support-priority labels.
    URGENT = "P0"
    HIGH = "P1"
    NORMAL = "P2"
    LOW = "P2"


class LeadFreshness(StrEnum):
    """Freshness of the presence observation used by a score."""

    CURRENT = "current"
    AGING = "aging"
    STALE = "stale"
    EXPIRED = "expired"


class LeadNextAction(StrEnum):
    """Bounded actions an operator can take from a score."""

    MONITOR = "monitor"
    MONITOR_CLOSELY = "monitor_closely"
    INVITE_CHAT = "invite_chat"
    CONTINUE_CONVERSATION = "continue_conversation"
    ANSWER_SHIPPING = "answer_shipping"
    ANSWER_PRICE = "answer_price"
    ANSWER_PAYMENT = "answer_payment"
    OFFER_ASSISTANCE = "offer_assistance"
    CONTACT_NOW = "contact_now"


class LeadPageTaxonomy(StrEnum):
    """Normalized page categories accepted by the scoring rules."""

    HOME = "home"
    PRODUCT = "product"
    CATEGORY = "category"
    COMPARISON = "comparison"
    CART = "cart"
    CHECKOUT = "checkout"
    PRICING = "pricing"
    SHIPPING = "shipping"
    PAYMENT = "payment"
    ORDER_CONFIRMATION = "order_confirmation"
    SUPPORT = "support"
    CONTENT = "content"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ConversationLeadContext:
    """Bounded commercial context projected from conversation working memory."""

    conversation_id: str
    last_intent: str | None = None
    sales_stage: str | None = None
    human_requested: bool = False

    @property
    def scoring_intent(self) -> str | None:
        if self.human_requested:
            return "human_handoff"
        if self.sales_stage == "purchase_ready":
            return "purchase_ready"
        return self.last_intent


@dataclass(frozen=True, slots=True)
class LeadScore:
    """Deterministic, explainable commercial-intent score for one visitor.

    ``signals`` contains stable rule identifiers rather than customer text.  The
    model intentionally has no tenant or identity authority; it is derived from a
    trusted :class:`VisitorPresence` adapter value by the application service.
    """

    commercial_intent: int
    tier: LeadIntentTier
    operation_priority: LeadOperationPriority
    confidence: float
    next_action: LeadNextAction
    signals: tuple[str, ...] = ()
    freshness: LeadFreshness = LeadFreshness.CURRENT
    rule_version: str = "lead-scoring.v1"
    scored_at: datetime | None = None
    current_page_dwell_seconds: int = 0
    session_active_dwell_seconds: int = 0
    session_age_seconds: int = 0
    page_kind: LeadPageTaxonomy = LeadPageTaxonomy.UNKNOWN
    data_coverage: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not 0 <= self.commercial_intent <= 100:
            raise ValueError("commercial_intent must be between 0 and 100")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if not self.rule_version.strip():
            raise ValueError("rule_version must not be empty")
        if (
            self.current_page_dwell_seconds < 0
            or self.session_active_dwell_seconds < 0
            or self.session_age_seconds < 0
        ):
            raise ValueError("dwell and age cannot be negative")

    @property
    def intent_score(self) -> int:
        """Compatibility name for consumers that call the value an intent score."""

        return self.commercial_intent

    @property
    def intent_tier(self) -> LeadIntentTier:
        """Long-form name used by API and dashboard projections."""

        return self.tier

    @property
    def score(self) -> int:
        return self.commercial_intent

    @property
    def high_intent(self) -> bool:
        """Compatibility alias for the canonical opportunity-queue decision."""

        return self.queue_eligible

    @property
    def queue_eligible(self) -> bool:
        """Whether fresh evidence qualifies for the live purchase-opportunity queue."""

        return self.tier in {LeadIntentTier.HOT, LeadIntentTier.WARM} and self.freshness in {
            LeadFreshness.CURRENT,
            LeadFreshness.AGING,
        }

    @property
    def confidence_grade(self) -> str:
        """Human-readable confidence band; this is not a conversion probability."""

        if self.confidence >= 0.8:
            return "A"
        if self.confidence >= 0.6:
            return "B"
        return "C"


# These aliases keep the domain vocabulary usable by callers that use the
# longer feature name while retaining one canonical value object.
LeadScoringResult = LeadScore
CommercialIntentScore = LeadScore
LeadTier = LeadIntentTier
OperationPriority = LeadOperationPriority
Freshness = LeadFreshness
NextAction = LeadNextAction
PageTaxonomy = LeadPageTaxonomy


__all__ = [
    "CommercialIntentScore",
    "ConversationLeadContext",
    "Freshness",
    "LeadFreshness",
    "LeadIntentTier",
    "LeadNextAction",
    "LeadOperationPriority",
    "LeadPageTaxonomy",
    "LeadScore",
    "LeadScoringResult",
    "LeadTier",
    "NextAction",
    "OperationPriority",
    "PageTaxonomy",
]
