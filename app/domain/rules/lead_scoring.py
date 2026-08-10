"""Deterministic commercial-intent scoring for live visitor presence.

The rule scores only bounded evidence available in the short-lived presence
stream. It does not claim to be a calibrated purchase probability; that
requires an outcome ledger and offline evaluation.
"""

from datetime import UTC, datetime
from math import exp
from urllib.parse import urlsplit

from app.domain.models import VisitorPresence
from app.domain.models.lead_scoring import (
    LeadFreshness,
    LeadIntentTier,
    LeadNextAction,
    LeadOperationPriority,
    LeadPageTaxonomy,
    LeadScore,
)

LEAD_SCORING_RULE_VERSION = "lead-scoring.v1"
RULE_VERSION = LEAD_SCORING_RULE_VERSION
COMMERCIAL_INTENT_THRESHOLD = 35
HIGH_INTENT_THRESHOLD = COMMERCIAL_INTENT_THRESHOLD
STRONG_SIGNAL_CAP = 50
STRONG_SIGNAL_THRESHOLD = 30
CATEGORY_CAPS = {
    "strong": STRONG_SIGNAL_CAP,
    "consideration": 20,
    "activity": 15,
    "freshness": 15,
}
LEAD_SCORING_CATEGORY_CAPS = CATEGORY_CAPS
_KNOWN_PAGE_KINDS = {item.value for item in LeadPageTaxonomy}
_MAX_AGE_SECONDS = 10 * 60
_STRONG_INTENTS = frozenset(
    {
        "buy",
        "buy_now",
        "cart",
        "checkout",
        "order",
        "payment",
        "purchase",
        "purchase_ready",
        "quote",
    }
)
_ACTION_INTENTS = frozenset({"human_handoff", "contact_human", "speak_to_human"})
_MEDIUM_INTENTS = frozenset(
    {
        "delivery",
        "delivery_estimate",
        "discount",
        "payment_methods",
        "price",
        "product_comparison",
        "product_customization",
        "product_dimensions",
        "product_material",
        "product_price",
        "product_recommendation",
        "product_stock",
        "product_weight",
        "shipping",
        "shipping_coverage",
        "shipping_customs",
        "stock",
    }
)
_KNOWN_INTENTS = _STRONG_INTENTS | _ACTION_INTENTS | _MEDIUM_INTENTS


def score_lead(
    presence: VisitorPresence,
    now: datetime | None = None,
    *,
    current_time: datetime | None = None,
    conversation_intent: str | None = None,
    page_taxonomy: str | None = None,
) -> LeadScore:
    """Score one presence observation using capped, explainable signals."""

    if now is not None and current_time is not None:
        raise TypeError("provide either now or current_time, not both")
    observed_at = _as_utc(current_time or now or datetime.now(UTC))
    last_seen = _as_utc(presence.last_seen_at)
    session_started = _as_utc(presence.session_started_at or presence.first_seen_at or last_seen)
    current_page_entered = _as_utc(presence.current_page_entered_at or last_seen)
    last_seen_age = _age_seconds(observed_at, last_seen)
    session_age = _age_seconds(observed_at, session_started)
    # Dwell ends at the last accepted heartbeat. Using ``observed_at`` here
    # would keep increasing the score after a visitor has left the page.
    current_page_dwell = _age_seconds(last_seen, current_page_entered)
    if presence.session_started_at is not None or presence.first_seen_at is not None:
        current_page_dwell = min(current_page_dwell, session_age)
    session_active_dwell = max(0, presence.session_active_dwell_seconds)
    if presence.session_started_at is not None or presence.first_seen_at is not None:
        session_active_dwell = min(
            session_active_dwell,
            _age_seconds(last_seen, session_started),
        )

    freshness = _freshness(last_seen_age)
    page_kind, page_is_trusted = _page_taxonomy(presence, page_taxonomy)
    page_views = max(1, int(presence.page_view_count or 1))
    normalized_intent = _normalise_token(conversation_intent)

    signals: list[str] = []
    coverage: list[str] = ["presence", "last_seen"]
    if presence.session_started_at is not None or presence.first_seen_at is not None:
        coverage.append("session_timing")
    if presence.current_page_entered_at is not None:
        coverage.append("page_timing")
    if presence.page_view_count:
        coverage.append("page_views")
    if session_active_dwell > 0:
        coverage.append("session_active_dwell")
    if page_is_trusted:
        signals.append("page_taxonomy")
        coverage.extend(("page_taxonomy", "trusted_page_taxonomy"))
    elif page_kind is not LeadPageTaxonomy.UNKNOWN:
        coverage.append("url_taxonomy")

    # Only connector-provided taxonomy can create a strong page signal. URL
    # parsing is a useful fallback, but a browser-controlled path cannot by
    # itself create Hot intent.
    strong = 0
    consideration = 0
    if page_kind is LeadPageTaxonomy.CHECKOUT:
        signals.append("checkout_page")
        coverage.append("checkout_page")
        if page_is_trusted:
            strong += 50
        else:
            consideration += 15
    elif page_kind is LeadPageTaxonomy.CART:
        signals.append("cart_page")
        coverage.append("cart_page")
        if page_is_trusted:
            strong += 35
        else:
            consideration += 14
    elif page_kind is LeadPageTaxonomy.ORDER_CONFIRMATION:
        signals.append("order_confirmation_page")
        coverage.append("post_purchase_page")

    if normalized_intent in _STRONG_INTENTS:
        strong += 40
        signals.append(f"intent_{normalized_intent}")
        coverage.append("conversation_intent")
    elif normalized_intent in _ACTION_INTENTS:
        signals.append(f"intent_{normalized_intent}")
        coverage.append("conversation_intent")

    strong = min(CATEGORY_CAPS["strong"], strong)

    # Product consideration is capped separately. Page depth appears only in
    # this category so it cannot be counted again as activity.
    if page_kind is LeadPageTaxonomy.PRODUCT:
        consideration += 16 if page_is_trusted else 12
        signals.append("product_page")
        coverage.append("product_page")
    elif page_kind in {
        LeadPageTaxonomy.PRICING,
        LeadPageTaxonomy.SHIPPING,
        LeadPageTaxonomy.PAYMENT,
    }:
        consideration += 10 if page_is_trusted else 8
        signals.append(f"{page_kind.value}_page")
        coverage.append("commercial_information_page")
    elif page_kind is LeadPageTaxonomy.CATEGORY:
        consideration += 4
        signals.append("category_page")
    elif page_kind is LeadPageTaxonomy.COMPARISON:
        consideration += 9
        signals.append("comparison_page")

    if normalized_intent in _MEDIUM_INTENTS:
        consideration += 6
        signals.append(f"intent_{normalized_intent}")
        coverage.append("conversation_intent")
    if page_views >= 5:
        consideration += 6
        signals.append("page_views_5")
    elif page_views >= 3:
        consideration += 3
        signals.append("page_views_3")
    consideration = min(CATEGORY_CAPS["consideration"], consideration)

    # Current-page dwell is derived from current_page_entered_at, never from
    # the age of the whole session. The category cap prevents dwell and widget
    # state from overwhelming weak commercial evidence.
    current_page_activity = 0
    if current_page_dwell >= 180:
        current_page_activity = 15
        signals.append("page_dwell_180s")
    elif current_page_dwell >= 60:
        current_page_activity = 10
        signals.append("page_dwell_60s")
    elif current_page_dwell >= 15:
        current_page_activity = 5
        signals.append("page_dwell_15s")
    session_activity = 0
    if session_active_dwell >= 180:
        session_activity = 15
        signals.append("session_active_180s")
    elif session_active_dwell >= 60:
        session_activity = 10
        signals.append("session_active_60s")
    elif session_active_dwell >= 15:
        session_activity = 5
        signals.append("session_active_15s")
    activity = max(current_page_activity, session_activity)
    if presence.widget_state == "open":
        activity += 3
        signals.append("widget_open")
        coverage.append("widget_state")
    activity = min(CATEGORY_CAPS["activity"], activity)

    # Recency improves ranking only when commercial evidence already exists.
    # A fresh heartbeat or linked support conversation is not purchase intent.
    recency = 0
    has_commercial_evidence = strong > 0 or consideration > 0
    if has_commercial_evidence and freshness is LeadFreshness.CURRENT:
        recency = 5
        signals.append("fresh_current")
    elif has_commercial_evidence and freshness is LeadFreshness.AGING:
        recency = 2
        signals.append("fresh_aging")
    else:
        signals.append(f"fresh_{freshness.value}")
    recency = min(CATEGORY_CAPS["freshness"], recency)

    if presence.conversation_id:
        signals.append("conversation_started")
        coverage.append("conversation_link")
        if normalized_intent in _KNOWN_INTENTS:
            signals.append(f"conversation_intent:{normalized_intent}")

    score = min(100, max(0, strong + consideration + activity + recency))
    if freshness is LeadFreshness.STALE:
        score = max(0, score - 12)
    elif freshness is LeadFreshness.EXPIRED:
        score = max(0, score - 25)
    if last_seen_age > 60:
        score = round(score * exp(-((last_seen_age - 60) / 900)))

    confidence = _confidence(
        page_is_trusted=page_is_trusted,
        page_kind=page_kind,
        freshness=freshness,
        has_session_started=presence.session_started_at is not None
        or presence.first_seen_at is not None,
        has_current_page=presence.current_page_entered_at is not None,
        has_conversation_intent=bool(normalized_intent),
    )
    if page_kind is LeadPageTaxonomy.UNKNOWN:
        signals.append("unknown_page")
    if freshness in {LeadFreshness.STALE, LeadFreshness.EXPIRED}:
        signals.append("stale_data")

    is_fresh = freshness in {LeadFreshness.CURRENT, LeadFreshness.AGING}
    if (
        strong >= STRONG_SIGNAL_THRESHOLD
        and score >= COMMERCIAL_INTENT_THRESHOLD
        and is_fresh
        and page_kind is not LeadPageTaxonomy.ORDER_CONFIRMATION
    ):
        tier = LeadIntentTier.HOT
    elif (
        score >= COMMERCIAL_INTENT_THRESHOLD
        and page_kind is not LeadPageTaxonomy.UNKNOWN
        and page_kind is not LeadPageTaxonomy.ORDER_CONFIRMATION
        and is_fresh
    ):
        tier = LeadIntentTier.WARM
    elif page_kind is LeadPageTaxonomy.UNKNOWN and strong == 0:
        tier = LeadIntentTier.UNKNOWN
    else:
        tier = LeadIntentTier.NURTURE

    operation_priority, next_action = _operation_decision(
        tier=tier,
        presence=presence,
        normalized_intent=normalized_intent,
    )

    return LeadScore(
        commercial_intent=score,
        tier=tier,
        operation_priority=operation_priority,
        confidence=confidence,
        next_action=next_action,
        signals=tuple(dict.fromkeys(signals)),
        freshness=freshness,
        scored_at=observed_at,
        current_page_dwell_seconds=current_page_dwell,
        session_active_dwell_seconds=session_active_dwell,
        session_age_seconds=session_age,
        page_kind=page_kind,
        data_coverage=tuple(dict.fromkeys(coverage)),
    )


def score_visitor_presence(
    presence: VisitorPresence,
    now: datetime | None = None,
    *,
    conversation_intent: object | None = None,
    page_taxonomy: object | None = None,
) -> LeadScore:
    """Presence-only adapter used by the live visitor application service."""

    return score_lead(
        presence,
        now,
        conversation_intent=_normalise_token(conversation_intent) or None,
        page_taxonomy=_normalise_token(page_taxonomy) or None,
    )


def calculate_lead_score(
    presence: VisitorPresence,
    now: datetime | None = None,
    *,
    conversation_intent: str | None = None,
    page_taxonomy: str | None = None,
) -> LeadScore:
    return score_lead(
        presence,
        now,
        conversation_intent=conversation_intent,
        page_taxonomy=page_taxonomy,
    )


def calculate_commercial_intent(
    presence: VisitorPresence,
    now: datetime | None = None,
    *,
    conversation_intent: str | None = None,
    page_taxonomy: str | None = None,
) -> int:
    return calculate_lead_score(
        presence,
        now,
        conversation_intent=conversation_intent,
        page_taxonomy=page_taxonomy,
    ).commercial_intent


def evaluate_lead_score(
    presence: VisitorPresence,
    now: datetime | None = None,
    *,
    conversation_intent: str | None = None,
    page_taxonomy: str | None = None,
) -> LeadScore:
    return calculate_lead_score(
        presence,
        now,
        conversation_intent=conversation_intent,
        page_taxonomy=page_taxonomy,
    )


def _operation_decision(
    *,
    tier: LeadIntentTier,
    presence: VisitorPresence,
    normalized_intent: str,
) -> tuple[LeadOperationPriority, LeadNextAction]:
    has_conversation = bool(presence.conversation_id)
    if normalized_intent in _ACTION_INTENTS and has_conversation:
        return LeadOperationPriority.P0, LeadNextAction.CONTACT_NOW
    if tier is LeadIntentTier.HOT and has_conversation:
        return LeadOperationPriority.P0, _conversation_action(normalized_intent)
    if has_conversation:
        return LeadOperationPriority.P1, _conversation_action(normalized_intent)
    if tier is LeadIntentTier.HOT:
        return LeadOperationPriority.P1, LeadNextAction.INVITE_CHAT
    if tier is LeadIntentTier.WARM or presence.widget_state == "open":
        return LeadOperationPriority.P1, LeadNextAction.OFFER_ASSISTANCE
    return LeadOperationPriority.P2, LeadNextAction.MONITOR


def _conversation_action(normalized_intent: str) -> LeadNextAction:
    if normalized_intent in {
        "delivery",
        "delivery_estimate",
        "shipping",
        "shipping_coverage",
        "shipping_customs",
    }:
        return LeadNextAction.ANSWER_SHIPPING
    if normalized_intent in {"discount", "price", "product_price", "quote"}:
        return LeadNextAction.ANSWER_PRICE
    if normalized_intent in {"payment", "payment_methods"}:
        return LeadNextAction.ANSWER_PAYMENT
    return LeadNextAction.CONTINUE_CONVERSATION


def _page_taxonomy(
    presence: VisitorPresence,
    page_taxonomy: object | None = None,
) -> tuple[LeadPageTaxonomy, bool]:
    configured = _normalise_token(page_taxonomy) or (presence.page_kind or "").strip().lower()
    configured = {
        "product_detail": "product",
        "product_page": "product",
        "cart_page": "cart",
        "checkout_page": "checkout",
        "order_confirmation_page": "order_confirmation",
    }.get(configured, configured)
    if configured in _KNOWN_PAGE_KINDS and configured != LeadPageTaxonomy.UNKNOWN.value:
        return LeadPageTaxonomy(configured), True

    path = urlsplit(presence.page_path or "/").path.lower().strip("/")
    segments = [segment for segment in path.split("/") if segment]
    if not segments:
        return LeadPageTaxonomy.HOME, False
    joined = "/".join(segments)
    if any(segment in {"checkout", "checkouts", "check-out"} for segment in segments):
        return LeadPageTaxonomy.CHECKOUT, False
    if any(segment in {"cart", "basket", "bag"} for segment in segments):
        return LeadPageTaxonomy.CART, False
    if any(
        segment in {"order-confirmation", "order-confirmed", "thank-you", "thankyou"}
        for segment in segments
    ):
        return LeadPageTaxonomy.ORDER_CONFIRMATION, False
    if any(segment in {"shipping", "delivery", "returns"} for segment in segments):
        return LeadPageTaxonomy.SHIPPING, False
    if any(segment in {"payment", "payments", "pricing", "price"} for segment in segments):
        return (
            LeadPageTaxonomy.PRICING
            if "pricing" in joined or "price" in joined
            else LeadPageTaxonomy.PAYMENT,
            False,
        )
    if any(segment in {"support", "help", "contact"} for segment in segments):
        return LeadPageTaxonomy.SUPPORT, False
    if any(segment in {"compare", "comparison"} for segment in segments):
        return LeadPageTaxonomy.COMPARISON, False
    if any(
        segment in {"category", "categories", "collection", "collections", "shop"}
        for segment in segments
    ):
        return LeadPageTaxonomy.CATEGORY, False
    if any(segment in {"product", "products", "item", "p"} for segment in segments):
        return LeadPageTaxonomy.PRODUCT, False
    # Locale-prefixed slugs are useful for common storefronts, but remain a
    # low-confidence fallback and can never create a strong signal.
    if len(segments) >= 2 and len(segments[0]) in {2, 5}:
        return LeadPageTaxonomy.PRODUCT, False
    return LeadPageTaxonomy.UNKNOWN, False


def _freshness(age_seconds: int) -> LeadFreshness:
    if age_seconds <= 60:
        return LeadFreshness.CURRENT
    if age_seconds <= 300:
        return LeadFreshness.AGING
    if age_seconds <= _MAX_AGE_SECONDS:
        return LeadFreshness.STALE
    return LeadFreshness.EXPIRED


def _confidence(
    *,
    page_is_trusted: bool,
    page_kind: LeadPageTaxonomy,
    freshness: LeadFreshness,
    has_session_started: bool,
    has_current_page: bool,
    has_conversation_intent: bool = False,
) -> float:
    value = 0.9 if page_is_trusted else 0.72 if page_kind is not LeadPageTaxonomy.UNKNOWN else 0.52
    if not has_session_started:
        value -= 0.12
    if not has_current_page:
        value -= 0.08
    if has_conversation_intent:
        value += 0.04
    if freshness is LeadFreshness.STALE:
        value -= 0.18
    elif freshness is LeadFreshness.EXPIRED:
        value -= 0.3
    return min(1.0, max(0.0, round(value, 2)))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _age_seconds(now: datetime, value: datetime) -> int:
    return max(0, int((now - value).total_seconds()))


def _normalise_token(value: object | None) -> str:
    if value is None:
        return ""
    if isinstance(value, (tuple, list)):
        value = value[0] if value else ""
    if isinstance(value, dict):
        for key in ("taxonomy", "category", "intent", "value", "name"):
            if key in value:
                value = value[key]
                break
    raw = getattr(value, "value", value)
    return "_".join(str(raw).strip().casefold().replace("-", "_").split())


__all__ = [
    "CATEGORY_CAPS",
    "COMMERCIAL_INTENT_THRESHOLD",
    "HIGH_INTENT_THRESHOLD",
    "LEAD_SCORING_CATEGORY_CAPS",
    "LEAD_SCORING_RULE_VERSION",
    "RULE_VERSION",
    "STRONG_SIGNAL_CAP",
    "STRONG_SIGNAL_THRESHOLD",
    "calculate_commercial_intent",
    "calculate_lead_score",
    "evaluate_lead_score",
    "score_lead",
    "score_visitor_presence",
]
