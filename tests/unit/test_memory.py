from app.domain.models import (
    ConversationSummary,
    ConversationTurn,
    ConversationWorkingMemory,
    DurableCustomerMemory,
    RecommendedProduct,
    ResponseKind,
)
from app.domain.rules import (
    extend_conversation_summary,
    extract_interaction_preferences,
    resolve_memory_reference,
    select_relevant_memories,
    update_working_memory,
)


def test_unresolved_sku_is_used_for_followup_clarification() -> None:
    resolution = resolve_memory_reference(
        message="What's the meaning?",
        memory=ConversationWorkingMemory(pending_product_sku="OVE01-1000"),
        language="en",
    )

    assert resolution.clarification is not None
    assert "OVE01-1000" in resolution.clarification
    assert "current product" in resolution.clarification


def test_active_product_resolves_entity_reference_but_not_ambiguous_meaning() -> None:
    entity = resolve_memory_reference(
        message="How about this model?",
        memory=ConversationWorkingMemory(active_product_sku="OVE01-21"),
        language="en",
    )
    meaning = resolve_memory_reference(
        message="What's the meaning?",
        memory=ConversationWorkingMemory(active_product_sku="OVE01-21"),
        language="en",
    )

    assert "PRODUCT_REFERENCE: OVE01-21" in entity.resolved_message
    assert meaning.clarification is not None
    assert "OVE01-21" in meaning.clarification


def test_working_memory_tracks_pending_then_resolved_product() -> None:
    pending = update_working_memory(
        current=ConversationWorkingMemory(),
        user_message="What is OVE01-1000?",
        resolved_message="What is OVE01-1000?",
        response_kind=ResponseKind.HANDOFF,
        response_language="en",
    )
    resolved = update_working_memory(
        current=pending,
        user_message="Here is the product OVE01-21",
        resolved_message="Here is the product OVE01-21",
        response_kind=ResponseKind.ANSWER,
        response_language="en",
    )

    assert pending.pending_product_sku == "OVE01-1000"
    assert pending.active_product_sku is None
    assert resolved.active_product_sku == "OVE01-21"
    assert resolved.pending_product_sku is None


def test_multiple_recommended_products_require_natural_reference_clarification() -> None:
    memory = update_working_memory(
        current=ConversationWorkingMemory(),
        user_message="I want a product delivered to the United States in USD.",
        resolved_message="I want a product delivered to the United States in USD.",
        response_kind=ResponseKind.ANSWER,
        response_language="en",
        candidate_product_skus=("SKU-100", "SKU-200"),
        recommended_products=(
            RecommendedProduct(sku="SKU-100", name="100cm C-cup", price="$269"),
            RecommendedProduct(sku="SKU-200", name="100cm B-cup", price="$279"),
        ),
    )

    resolution = resolve_memory_reference(
        message="How long does your product take to arrive?",
        memory=memory,
        language="en",
    )

    assert memory.candidate_product_skus == ("SKU-100", "SKU-200")
    assert memory.candidate_product_labels == ("100cm C-cup ($269)", "100cm B-cup ($279)")
    assert memory.country_code == "US"
    assert memory.currency == "USD"
    assert memory.confirmed_fields == ("country", "currency")
    assert resolution.clarification is not None
    assert "100cm C-cup ($269)" in resolution.clarification
    assert "100cm B-cup ($279)" in resolution.clarification
    assert "SKU-100" not in resolution.clarification


def test_ordinal_candidate_reference_resolves_without_clarification() -> None:
    memory = ConversationWorkingMemory(
        candidate_product_skus=("SKU-100", "SKU-200", "SKU-300"),
        candidate_products=(
            RecommendedProduct(sku="SKU-100", name="First option", price="399"),
            RecommendedProduct(sku="SKU-200", name="Second option", price="349"),
            RecommendedProduct(sku="SKU-300", name="Third option", price="299"),
        ),
    )

    resolution = resolve_memory_reference(
        message="第二款是什么材质？",
        memory=memory,
        language="zh",
    )

    assert resolution.clarification is None
    assert "PRODUCT_REFERENCE: SKU-200" in resolution.resolved_message


def test_pair_reference_resolves_the_two_recent_candidates() -> None:
    memory = ConversationWorkingMemory(
        candidate_product_skus=("SKU-100", "SKU-200", "SKU-300"),
    )

    resolution = resolve_memory_reference(
        message="这两款有什么区别？",
        memory=memory,
        language="zh",
    )

    assert resolution.clarification is None
    assert resolution.resolved_message.count("PRODUCT_REFERENCE:") == 2
    assert "PRODUCT_REFERENCE: SKU-100" in resolution.resolved_message
    assert "PRODUCT_REFERENCE: SKU-200" in resolution.resolved_message


def test_single_letter_catalog_sku_is_preserved_in_memory_reference() -> None:
    memory = ConversationWorkingMemory(
        candidate_product_skus=("D43079", "D03055"),
    )

    resolution = resolve_memory_reference(
        message="这两款有什么区别？",
        memory=memory,
        language="zh",
    )

    assert "PRODUCT_REFERENCE: D43079" in resolution.resolved_message
    assert "PRODUCT_REFERENCE: D03055" in resolution.resolved_message


def test_relative_price_and_weight_references_use_structured_candidate_facts() -> None:
    memory = ConversationWorkingMemory(
        active_product_sku="SKU-100",
        candidate_product_skus=("SKU-100", "SKU-200", "SKU-300"),
        candidate_products=(
            RecommendedProduct(sku="SKU-100", name="Current", price="399", weight="32 kg"),
            RecommendedProduct(sku="SKU-200", name="Cheaper", price="299", weight="28 kg"),
            RecommendedProduct(sku="SKU-300", name="Lightest", price="329", weight="20 kg"),
        ),
    )

    cheaper = resolve_memory_reference(message="便宜一点的呢？", memory=memory, language="zh")
    lighter = resolve_memory_reference(
        message="Do you have a lighter one?",
        memory=memory,
        language="en",
    )

    assert "PRODUCT_REFERENCE: SKU-200" in cheaper.resolved_message
    assert "PRODUCT_REFERENCE: SKU-300" in lighter.resolved_message


def test_relative_recommendation_changes_active_product_without_losing_candidates() -> None:
    candidates = (
        RecommendedProduct(sku="SKU-100", name="Lighter", price="399", weight="20 kg"),
        RecommendedProduct(sku="SKU-200", name="Cheaper", price="299", weight="28 kg"),
    )
    memory = ConversationWorkingMemory(
        active_product_sku="SKU-100",
        candidate_product_skus=("SKU-100", "SKU-200"),
        candidate_products=candidates,
    )

    updated = update_working_memory(
        current=memory,
        user_message="便宜一点的呢？",
        resolved_message="便宜一点的呢？\nPRODUCT_REFERENCE: SKU-200",
        response_kind=ResponseKind.ANSWER,
        response_language="zh",
        candidate_product_skus=("SKU-200",),
        recommended_products=(candidates[1],),
    )

    assert updated.active_product_sku == "SKU-200"
    assert updated.candidate_product_skus == ("SKU-100", "SKU-200")
    assert updated.candidate_products == candidates
    lighter = resolve_memory_reference(message="轻一点的呢？", memory=updated, language="zh")
    assert "PRODUCT_REFERENCE: SKU-100" in lighter.resolved_message


def test_relative_reference_without_candidates_requests_a_comparison_baseline() -> None:
    resolution = resolve_memory_reference(
        message="便宜一点的呢？",
        memory=ConversationWorkingMemory(),
        language="zh",
    )

    assert resolution.clarification is not None
    assert "可可靠比较的候选商品" in resolution.clarification
    assert "PRODUCT_REFERENCE" not in resolution.resolved_message


def test_single_recommended_product_becomes_active_reference() -> None:
    memory = update_working_memory(
        current=ConversationWorkingMemory(),
        user_message="Please recommend one.",
        resolved_message="Please recommend one.",
        response_kind=ResponseKind.ANSWER,
        response_language="en",
        candidate_product_skus=("SKU-100",),
        answer_missing_fields=("country",),
    )

    resolution = resolve_memory_reference(
        message="What material is this product?",
        memory=memory,
        language="en",
    )

    assert memory.active_product_sku == "SKU-100"
    assert memory.missing_fields == ("country",)
    assert "PRODUCT_REFERENCE: SKU-100" in resolution.resolved_message


def test_summary_only_contains_bounded_structured_history() -> None:
    turns = tuple(
        ConversationTurn(role="user", content=f"Tell me about SKU-{index:03d}")
        for index in range(1, 7)
    )
    summary = extend_conversation_summary(ConversationSummary(), turns)

    assert summary.summarized_message_count == 6
    assert summary.discussed_product_skus == tuple(f"SKU-{index:03d}" for index in range(1, 7))
    assert len(summary.compact_text) <= 1000


def test_structured_recommendations_are_saved_without_parsing_assistant_markdown() -> None:
    memory = update_working_memory(
        current=ConversationWorkingMemory(),
        user_message="Please recommend one.",
        resolved_message="Please recommend one.",
        response_kind=ResponseKind.ANSWER,
        response_language="en",
        candidate_product_skus=("SKU-IGNORED",),
        recommended_products=(
            RecommendedProduct(
                sku="SKU-100",
                name="Compact TPE model",
                price="279",
                currency="USD",
            ),
        ),
    )

    assert memory.candidate_product_skus == ("SKU-100",)
    assert memory.candidate_product_labels == ("Compact TPE model (USD 279)",)
    assert memory.candidate_products[0].sku == "SKU-100"


def test_sku_candidates_without_structured_products_do_not_invent_labels() -> None:
    memory = update_working_memory(
        current=ConversationWorkingMemory(),
        user_message="Please compare the options.",
        resolved_message="Please compare the options.",
        response_kind=ResponseKind.ANSWER,
        response_language="en",
        candidate_product_skus=("SKU-100", "SKU-200"),
    )

    assert memory.candidate_product_skus == ("SKU-100", "SKU-200")
    assert memory.candidate_product_labels == ()
    assert memory.candidate_products == ()


def test_relevant_memory_selection_drops_transactional_memory_and_bounds_results() -> None:
    memories = tuple(
        DurableCustomerMemory(kind="preference", content="prefers TPE") for _ in range(8)
    ) + (DurableCustomerMemory(kind="resolution", content="previous refund result"),)

    assert select_relevant_memories(memories, message="What is my order status?") == ()
    selected = select_relevant_memories(memories, message="Recommend a TPE model")
    assert len(selected) <= 5


def test_sales_memory_tracks_confirmed_preferences_questions_and_recent_phrases() -> None:
    memory = update_working_memory(
        current=ConversationWorkingMemory(),
        user_message="I prefer lightweight TPE and my budget is under $500.",
        resolved_message="I prefer lightweight TPE and my budget is under $500.",
        response_kind=ResponseKind.ANSWER,
        response_language="en",
        assistant_message="I can narrow this down. What height do you prefer?",
    )

    facts = {item.key: item for item in memory.preference_facts}
    assert facts["material"].value == "TPE"
    assert facts["material"].status.value == "confirmed"
    assert facts["handling"].value == "lightweight"
    assert facts["budget_max"].value == "$500"
    assert facts["budget_max"].source_revision == 1
    assert memory.question_ledger[0].key == "size"
    assert memory.recent_response_phrases


def test_interaction_preferences_are_explicit_bounded_and_replaceable() -> None:
    assert extract_interaction_preferences("请简短一点，直接说结论，不要推销") == (
        "response_length=concise",
        "answer_order=direct",
        "sales_tone=no_sales",
    )
    concise = update_working_memory(
        current=ConversationWorkingMemory(),
        user_message="请简短一点，直接说结论，不要推销",
        resolved_message="请简短一点，直接说结论，不要推销",
        response_kind=ResponseKind.ANSWER,
        response_language="zh",
    )
    detailed = update_working_memory(
        current=concise,
        user_message="这次请详细说明",
        resolved_message="这次请详细说明",
        response_kind=ResponseKind.ANSWER,
        response_language="zh",
    )

    assert concise.interaction_preferences == (
        "answer_order=direct",
        "response_length=concise",
        "sales_tone=no_sales",
    )
    assert detailed.interaction_preferences == (
        "answer_order=direct",
        "response_length=detailed",
        "sales_tone=no_sales",
    )
