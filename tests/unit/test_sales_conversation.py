from datetime import UTC, datetime

from app.application.services import SalesConversationService
from app.domain.models import (
    AnswerNextAction,
    AnswerPlan,
    AuthenticatedPrincipal,
    ConversationTurn,
    ConversationWorkingMemory,
    FactSourceMode,
    MemoryFactStatus,
    QuestionLedgerItem,
    RecommendedProduct,
    SalesMemoryFact,
    SalesNextAction,
    SalesStage,
    SupportIntent,
)
from app.domain.rules import classify_conversation_intent, merge_current_turn_sales_memory


def _principal(**overrides: object) -> AuthenticatedPrincipal:
    values = {
        "subject_id": "visitor",
        "tenant_id": "tenant-a",
        "roles": frozenset({"anonymous"}),
        "scopes": frozenset({"knowledge:read"}),
        "authentication_method": "public_widget_token",
        "authenticated_at": datetime.now(UTC),
        "correlation_id": "corr-1",
        "site_id": "shop-a",
        "preferred_language": "de",
        "site_domain": "https://shop.example.com",
        "agent_display_name": "Mia",
        "customer_address_mode": "formal",
    }
    values.update(overrides)
    return AuthenticatedPrincipal(**values)  # type: ignore[arg-type]


def _answer_plan(intent: SupportIntent) -> AnswerPlan:
    return AnswerPlan(
        intent=intent,
        product_ids=(),
        confirmed_facts=(),
        missing_fields=(),
        claims=(),
        next_action=AnswerNextAction.ANSWER,
        source_mode=FactSourceMode.KNOWLEDGE,
        max_words=120,
        response_style="answer_first",
    )


def test_site_identity_uses_only_trusted_principal_fields() -> None:
    service = SalesConversationService()

    profile = service.resolve_site_identity(_principal())

    assert profile.domain == "shop.example.com"
    assert profile.agent_display_name == "Mia"
    assert profile.customer_address_mode == "formal"


def test_current_customer_language_overrides_default_and_preserves_previous_language() -> None:
    service = SalesConversationService()

    context = service.resolve_language_context(
        message="请用中文回答，这款产品是什么材质？",
        history=(ConversationTurn(role="user", content="Ist dieses Modell auf Lager?"),),
        preferred_language="de",
        previous_language="de",
        address_mode="formal",
    )

    assert context.target_language == "zh"
    assert context.language_source == "customer_explicit"
    assert context.previous_language == "de"
    assert context.language_switched


def test_product_code_alone_does_not_switch_the_stable_conversation_language() -> None:
    service = SalesConversationService()

    context = service.resolve_language_context(
        message="SKU-ABC123",
        history=(ConversationTurn(role="user", content="Bitte auf Deutsch antworten."),),
        preferred_language="en",
        previous_language="de",
        address_mode="neutral",
    )

    assert context.target_language == "de"
    assert context.language_source == "conversation_memory"
    assert not context.language_switched


def test_language_switch_preserves_existing_sales_memory() -> None:
    service = SalesConversationService()
    memory = merge_current_turn_sales_memory(
        ConversationWorkingMemory(),
        message="我的预算大约是1500美元，偏好硅胶。",
    )
    context = service.resolve_language_context(
        message="Please continue in English. I also need it to be easy to store.",
        history=(ConversationTurn(role="user", content="请用中文回答。"),),
        preferred_language="zh",
        previous_language="zh",
        address_mode="neutral",
    )
    memory = merge_current_turn_sales_memory(
        memory,
        message="Please continue in English. I also need it to be easy to store.",
    )

    facts = {(item.key, item.value) for item in memory.preference_facts}
    assert context.target_language == "en"
    assert context.language_switched
    assert ("budget_max", "1500") in facts
    assert ("material", "SILICONE") in facts
    assert ("storage", "compact") in facts


def test_composite_recommendation_is_not_misclassified_as_care() -> None:
    assert (
        classify_conversation_intent("请推荐一款硅胶、轻一点、容易收纳的产品。")
        == "product_recommendation"
    )


def test_sales_plan_reuses_confirmed_preferences_and_asks_only_unasked_question() -> None:
    service = SalesConversationService()
    principal = _principal()
    identity = service.resolve_site_identity(principal)
    language = service.resolve_language_context(
        message="Please recommend a lightweight model.",
        history=(),
        preferred_language="en",
        previous_language="en",
        address_mode="neutral",
    )
    memory = ConversationWorkingMemory(
        primary_goal="product_recommendation",
        preference_facts=(
            SalesMemoryFact("handling", "lightweight", MemoryFactStatus.CONFIRMED, 1),
        ),
        question_ledger=(QuestionLedgerItem("budget", "What is your budget?", "asked", 1),),
        revision=2,
    )
    answer_plan = AnswerPlan(
        intent=SupportIntent.PRODUCT_RECOMMENDATION,
        product_ids=("SKU-1", "SKU-2", "SKU-3", "SKU-4"),
        confirmed_facts=(),
        missing_fields=("budget", "size"),
        claims=(),
        next_action=AnswerNextAction.CLARIFY,
        source_mode=FactSourceMode.MIXED,
        max_words=140,
        response_style="recommendation",
    )

    plan = service.plan_response(
        message="Please recommend a lightweight model.",
        answer_plan=answer_plan,
        memory=memory,
        language_context=language,
        site_identity=identity,
    )

    assert plan.sales_stage is SalesStage.RECOMMENDATION
    assert plan.confirmed_preferences == ("handling=lightweight",)
    assert plan.recommended_product_ids == ("SKU-1", "SKU-2", "SKU-3")
    assert plan.follow_up_question_key == "size"
    assert plan.next_best_action is SalesNextAction.ASK_ONE_QUESTION
    assert "recommend_up_to_three_options" in plan.response_moves
    assert "ask_one_follow_up:size" in plan.response_moves
    assert not plan.should_introduce


def test_order_plan_never_advances_the_sale() -> None:
    service = SalesConversationService()
    identity = service.resolve_site_identity(_principal())
    language = service.resolve_language_context(
        message="Where is my order?",
        history=(),
        preferred_language="en",
        previous_language="en",
        address_mode="neutral",
    )
    plan = _answer_plan(SupportIntent.ORDER_STATUS)
    plan = AnswerPlan(
        intent=plan.intent,
        product_ids=plan.product_ids,
        confirmed_facts=plan.confirmed_facts,
        missing_fields=("order_reference",),
        claims=plan.claims,
        next_action=AnswerNextAction.HANDOFF,
        source_mode=FactSourceMode.REALTIME,
        max_words=70,
        response_style="task_status",
    )

    sales_plan = service.plan_response(
        message="Where is my order?",
        answer_plan=plan,
        memory=ConversationWorkingMemory(),
        language_context=language,
        site_identity=identity,
    )

    assert sales_plan.sales_stage is SalesStage.HUMAN_HANDOFF
    assert sales_plan.next_best_action is SalesNextAction.HANDOFF
    assert sales_plan.handoff_required


def test_sales_plan_contains_auditable_recommendation_explanations() -> None:
    service = SalesConversationService()
    identity = service.resolve_site_identity(_principal())
    language = service.resolve_language_context(
        message="Recommend one under $500.",
        history=(),
        preferred_language="en",
        previous_language="en",
        address_mode="neutral",
    )
    memory = ConversationWorkingMemory(
        candidate_products=(
            RecommendedProduct(
                sku="SKU-1",
                name="Compact silicone model",
                match_reasons=("material_match", "within_budget"),
                tradeoffs=("weight_unknown",),
                missing_facts=("weight",),
            ),
        ),
    )
    answer_plan = AnswerPlan(
        intent=SupportIntent.PRODUCT_RECOMMENDATION,
        product_ids=("SKU-1",),
        confirmed_facts=(),
        missing_fields=(),
        claims=(),
        next_action=AnswerNextAction.ANSWER,
        source_mode=FactSourceMode.MIXED,
        max_words=140,
        response_style="recommendation",
    )

    plan = service.plan_response(
        message="Recommend one under $500.",
        answer_plan=answer_plan,
        memory=memory,
        language_context=language,
        site_identity=identity,
    )

    assert plan.recommendation_reasons == (
        "SKU-1:match=material_match,within_budget;tradeoff=weight_unknown;missing=weight",
    )
    assert plan.playbook_id == "recommendation"
    assert "product_attributes" in plan.playbook_evidence_slots
