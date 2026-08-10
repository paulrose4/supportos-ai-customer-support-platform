from datetime import UTC, datetime, timedelta

from app.application.dto import LeadScoreResult, ScoreLeadCommand
from app.application.services import LeadScoringService
from app.domain.models import (
    LeadFreshness,
    LeadIntentTier,
    LeadNextAction,
    LeadOperationPriority,
    LeadScore,
    VisitorPresence,
)
from app.domain.rules import CATEGORY_CAPS, score_lead

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


def presence(
    *,
    path: str = "/",
    page_kind: str | None = None,
    last_seen_at: datetime = NOW,
    first_seen_at: datetime | None = None,
    current_page_entered_at: datetime | None = None,
    page_view_count: int = 1,
    conversation_id: str | None = None,
    widget_state: str = "closed",
    session_active_dwell_seconds: int = 0,
) -> VisitorPresence:
    return VisitorPresence(
        tenant_id="tenant-a",
        site_id="site-a",
        visitor_id="visitor-a",
        conversation_id=conversation_id,
        page_path=path,
        page_kind=page_kind,
        last_seen_at=last_seen_at,
        first_seen_at=first_seen_at,
        page_view_count=page_view_count,
        current_page_entered_at=current_page_entered_at,
        widget_state=widget_state,
        session_active_dwell_seconds=session_active_dwell_seconds,
    )


def test_trusted_checkout_is_hot_but_commercial_and_operational_bands_stay_separate() -> None:
    score = score_lead(
        presence(
            path="/opaque",
            page_kind="checkout",
            first_seen_at=NOW - timedelta(minutes=5),
            current_page_entered_at=NOW - timedelta(minutes=3),
            page_view_count=5,
            conversation_id="conversation-a",
        ),
        NOW,
    )

    assert isinstance(score, LeadScore)
    assert score.commercial_intent == 76
    assert score.tier is LeadIntentTier.HOT
    assert score.operation_priority is LeadOperationPriority.P0
    assert score.next_action is LeadNextAction.CONTINUE_CONVERSATION
    assert score.queue_eligible is True
    assert score.freshness is LeadFreshness.CURRENT
    assert score.confidence_grade == "A"
    assert "checkout_page" in score.signals
    assert "trusted_page_taxonomy" in score.data_coverage


def test_url_checkout_fallback_cannot_create_hot_intent() -> None:
    score = score_lead(
        presence(
            path="/checkout",
            current_page_entered_at=NOW - timedelta(minutes=3),
            page_view_count=5,
        ),
        NOW,
    )

    assert score.commercial_intent == 40
    assert score.tier is LeadIntentTier.WARM
    assert score.operation_priority is LeadOperationPriority.P1
    assert score.confidence_grade == "B"
    assert "url_taxonomy" in score.data_coverage


def test_current_page_entered_at_is_used_instead_of_first_seen_at() -> None:
    score = score_lead(
        presence(
            path="/products/widget",
            first_seen_at=NOW - timedelta(minutes=10),
            current_page_entered_at=NOW - timedelta(seconds=10),
        ),
        NOW,
    )

    assert score.commercial_intent == 17
    assert score.current_page_dwell_seconds == 10
    assert "page_dwell_180s" not in score.signals


def test_current_page_dwell_stops_at_last_heartbeat() -> None:
    score = score_lead(
        presence(
            path="/products/widget",
            last_seen_at=NOW - timedelta(minutes=2),
            first_seen_at=NOW - timedelta(minutes=5),
            current_page_entered_at=NOW - timedelta(seconds=150),
        ),
        NOW,
    )

    assert score.current_page_dwell_seconds == 30
    assert "page_dwell_60s" not in score.signals


def test_session_active_dwell_survives_navigation_without_double_counting_activity() -> None:
    score = score_lead(
        presence(
            path="/products/widget",
            first_seen_at=NOW - timedelta(minutes=4),
            current_page_entered_at=NOW - timedelta(seconds=10),
            session_active_dwell_seconds=180,
        ),
        NOW,
    )

    assert score.session_active_dwell_seconds == 180
    assert "session_active_180s" in score.signals
    assert score.commercial_intent == 32


def test_weak_generic_browsing_does_not_enter_the_opportunity_queue() -> None:
    score = score_lead(
        presence(
            path="/editorial/story",
            current_page_entered_at=NOW - timedelta(minutes=3),
            page_view_count=5,
        ),
        NOW,
    )

    assert score.commercial_intent == 26
    assert score.tier is LeadIntentTier.UNKNOWN
    assert score.queue_eligible is False


def test_product_browsing_can_be_warm_but_never_hot_without_a_strong_signal() -> None:
    score = score_lead(
        presence(
            path="/products/widget",
            current_page_entered_at=NOW - timedelta(minutes=3),
            page_view_count=5,
        ),
        NOW,
    )

    assert score.commercial_intent == 38
    assert score.tier is LeadIntentTier.WARM
    assert score.operation_priority is LeadOperationPriority.P1


def test_category_caps_are_applied_before_total_score() -> None:
    score = score_lead(
        presence(
            path="/opaque",
            page_kind="product",
            current_page_entered_at=NOW - timedelta(minutes=4),
            page_view_count=20,
            widget_state="open",
        ),
        NOW,
        conversation_intent="purchase_ready",
    )

    assert score.commercial_intent == 80
    assert score.commercial_intent <= sum(CATEGORY_CAPS.values())


def test_linked_conversation_is_operational_evidence_not_purchase_intent() -> None:
    score = score_lead(presence(path="/", conversation_id="conversation-a"), NOW)

    assert score.commercial_intent == 0
    assert score.tier is LeadIntentTier.NURTURE
    assert score.operation_priority is LeadOperationPriority.P1
    assert "conversation_started" in score.signals


def test_explicit_purchase_intent_is_hot_and_explained() -> None:
    score = score_lead(
        presence(path="/", conversation_id="conversation-a"),
        NOW,
        conversation_intent="purchase_ready",
    )

    assert score.commercial_intent == 45
    assert score.tier is LeadIntentTier.HOT
    assert score.operation_priority is LeadOperationPriority.P0
    assert "conversation_intent:purchase_ready" in score.signals


def test_human_request_changes_priority_without_faking_purchase_intent() -> None:
    score = score_lead(
        presence(path="/support/topic", conversation_id="conversation-a"),
        NOW,
        conversation_intent="speak_to_human",
    )

    assert score.commercial_intent == 0
    assert score.tier is LeadIntentTier.NURTURE
    assert score.operation_priority is LeadOperationPriority.P0
    assert score.next_action is LeadNextAction.CONTACT_NOW


def test_unknown_conversation_intent_is_not_exposed_as_a_signal() -> None:
    score = score_lead(
        presence(path="/", conversation_id="conversation-a"),
        NOW,
        conversation_intent="unexpected customer-derived value",
    )

    assert score.commercial_intent == 0
    assert score.signals == ("fresh_current", "conversation_started")


def test_time_decay_reduces_old_presence_score_and_removes_queue_eligibility() -> None:
    recent = score_lead(
        presence(
            path="/opaque",
            page_kind="cart",
            first_seen_at=NOW - timedelta(minutes=4),
            current_page_entered_at=NOW - timedelta(minutes=3),
        ),
        NOW,
    )
    stale = score_lead(
        presence(
            path="/opaque",
            page_kind="cart",
            last_seen_at=NOW - timedelta(minutes=12),
            first_seen_at=NOW - timedelta(minutes=15),
            current_page_entered_at=NOW - timedelta(minutes=15),
        ),
        NOW,
    )

    assert recent.commercial_intent > stale.commercial_intent
    assert stale.freshness is LeadFreshness.EXPIRED
    assert stale.queue_eligible is False
    assert stale.confidence < recent.confidence


def test_taxonomy_can_supply_product_signal_when_path_is_generic() -> None:
    score = score_lead(
        presence(path="/x", first_seen_at=NOW, current_page_entered_at=NOW),
        NOW,
        page_taxonomy="product_detail",
    )

    assert score.commercial_intent == 21
    assert score.confidence_grade == "A"
    assert "product_page" in score.signals
    assert "page_taxonomy" in score.signals


def test_application_service_maps_domain_result_to_application_dto() -> None:
    result = LeadScoringService().score(
        ScoreLeadCommand(
            presence=presence(
                path="/cart",
                first_seen_at=NOW,
                current_page_entered_at=NOW,
            ),
            now=NOW,
            page_taxonomy="cart",
        )
    )

    assert isinstance(result, LeadScoreResult)
    assert result.commercial_intent == result.score == result.intent_score == 40
    assert result.domain_score.commercial_intent == 40
    assert result.queue_eligible is True
    assert result.confidence_grade == "A"
    assert result.as_dict()["tier"] == "hot"
    assert result.as_dict()["operation_priority"] == "P1"


def test_naive_datetimes_are_treated_as_utc() -> None:
    naive_now = datetime(2026, 8, 5, 12, 0)
    score = score_lead(presence(last_seen_at=naive_now), naive_now)

    assert score.freshness is LeadFreshness.CURRENT
