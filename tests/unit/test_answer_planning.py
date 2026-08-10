from app.domain.models import AnswerNextAction, FactSourceMode, SupportIntent
from app.domain.rules import build_answer_plan, classify_support_intent


def test_support_intent_classification_covers_commerce_and_support_tasks() -> None:
    assert (
        classify_support_intent("How much does this product cost?") is SupportIntent.PRODUCT_PRICE
    )
    assert classify_support_intent("How many days is delivery?") is SupportIntent.DELIVERY_ESTIMATE
    assert classify_support_intent("Where is my order ORDER-1001?") is SupportIntent.ORDER_STATUS
    assert (
        classify_support_intent("Please connect me to a human agent") is SupportIntent.HUMAN_HANDOFF
    )
    assert (
        classify_support_intent("Compare those two by weight and price")
        is SupportIntent.PRODUCT_COMPARISON
    )
    assert (
        classify_support_intent("便宜一点的呢？\nPRODUCT_REFERENCE: D03055")
        is SupportIntent.PRODUCT_RECOMMENDATION
    )


def test_answer_plan_always_hands_order_requests_to_human_support() -> None:
    missing = build_answer_plan(message="Where is my order?")
    complete = build_answer_plan(message="Where is my order ORDER-1001?")

    assert missing.intent is SupportIntent.ORDER_STATUS
    assert missing.source_mode is FactSourceMode.REALTIME
    assert missing.missing_fields == ("order_reference",)
    assert missing.next_action is AnswerNextAction.HANDOFF
    assert complete.next_action is AnswerNextAction.HANDOFF


def test_public_return_policy_remains_a_knowledge_question() -> None:
    plan = build_answer_plan(message="What is your refund policy?")

    assert plan.intent is SupportIntent.RETURN_POLICY
    assert plan.next_action is AnswerNextAction.ANSWER


def test_answer_plan_bounds_and_deduplicates_product_context() -> None:
    plan = build_answer_plan(
        message="Compare these products",
        product_ids=("SKU-1", "SKU-1", "SKU-2"),
    )

    assert plan.intent is SupportIntent.PRODUCT_COMPARISON
    assert plan.product_ids == ("SKU-1", "SKU-2")
    assert plan.max_words == 150
    assert plan.response_style == "comparison"


def test_industry_support_intents_are_classified_before_general_fallback() -> None:
    assert classify_support_intent("Is the package discreet?") is SupportIntent.PRIVACY_PACKAGING
    assert classify_support_intent("Will the package reveal what is inside?") is (
        SupportIntent.PRIVACY_PACKAGING
    )
    assert classify_support_intent("Is this site legit or a scam?") is SupportIntent.SITE_TRUST
    assert classify_support_intent("Do I have to pay customs duty?") is (
        SupportIntent.SHIPPING_CUSTOMS
    )
    assert classify_support_intent("Can this model be customized?") is (
        SupportIntent.PRODUCT_CUSTOMIZATION
    )
