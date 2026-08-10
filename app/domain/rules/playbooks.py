from app.domain.models import ConversationPlaybookCard, SupportIntent

_COMMON_PROHIBITIONS = (
    "invented_discount",
    "fake_urgency",
    "absolute_guarantee",
    "unsupported_popularity_claim",
)

_CARDS = {
    "recommendation": ConversationPlaybookCard(
        "recommendation",
        "narrow the choice to evidence-backed products",
        ("answer_first", "recommend_up_to_three", "state_relevant_tradeoff", "one_next_step"),
        ("product_attributes", "price", "weight", "material"),
        "highest_value_missing_preference",
        _COMMON_PROHIBITIONS,
    ),
    "comparison": ConversationPlaybookCard(
        "comparison",
        "explain decision-relevant differences",
        ("compare_customer_priorities", "state_tradeoffs", "recommend_by_fit"),
        ("exact_product_facts", "price", "weight", "material", "dimensions"),
        None,
        _COMMON_PROHIBITIONS,
    ),
    "care_storage": ConversationPlaybookCard(
        "care_storage",
        "provide material-appropriate low-risk care",
        ("answer_with_approved_sop", "identify_material_limits", "offer_model_check"),
        ("product_material", "approved_care_sop"),
        "product_reference",
        (*_COMMON_PROHIBITIONS, "unsafe_repair_instruction"),
        risk_level=1,
    ),
    "privacy_packaging": ConversationPlaybookCard(
        "privacy_packaging",
        "answer privacy concerns only from published policy",
        ("acknowledge_specific_concern", "quote_published_packaging_and_data_policy"),
        ("packaging_policy", "billing_descriptor", "privacy_policy"),
        None,
        (*_COMMON_PROHIBITIONS, "absolute_privacy_guarantee"),
    ),
    "site_trust": ConversationPlaybookCard(
        "site_trust",
        "offer verifiable trust evidence without persuasion tricks",
        ("acknowledge_specific_concern", "offer_verifiable_contacts_and_policies"),
        ("trusted_domain", "contact_page", "payment_policy", "return_policy"),
        None,
        (*_COMMON_PROHIBITIONS, "invented_sales_or_company_age"),
    ),
    "shipping_customs": ConversationPlaybookCard(
        "shipping_customs",
        "explain published shipping scope and customs uncertainty",
        ("ask_country_if_missing", "state_published_scope", "avoid_tax_guarantee"),
        ("customer_confirmed_country", "shipping_policy", "customs_policy"),
        "country",
        (*_COMMON_PROHIBITIONS, "guaranteed_delivery", "tax_free_guarantee"),
    ),
    "customization": ConversationPlaybookCard(
        "customization",
        "separate published options from bespoke requests",
        ("state_published_options", "collect_one_key_requirement", "handoff_bespoke_quote"),
        ("product_options", "customization_policy"),
        "customization_requirement",
        (*_COMMON_PROHIBITIONS, "invented_custom_option", "unapproved_quote"),
    ),
    "price_objection": ConversationPlaybookCard(
        "price_objection",
        "explain relevant value or provide a verified lower-price alternative",
        ("acknowledge_budget", "explain_relevant_value", "offer_verified_alternative"),
        ("price", "customer_budget", "comparable_attributes"),
        None,
        (*_COMMON_PROHIBITIONS, "unapproved_discount"),
    ),
    "order_handoff": ConversationPlaybookCard(
        "order_handoff",
        "stop sales progression and transfer safely",
        ("collect_minimum_safe_reference", "handoff"),
        ("trusted_order_system",),
        "order_reference",
        (*_COMMON_PROHIBITIONS, "order_status_inference", "payment_data_request"),
        risk_level=2,
    ),
    "general_support": ConversationPlaybookCard(
        "general_support",
        "answer the current shopping question directly",
        ("answer_first", "one_next_step"),
        ("relevant_published_evidence",),
        None,
        _COMMON_PROHIBITIONS,
    ),
}


def select_conversation_playbook(
    *,
    intent: SupportIntent,
    objection: str | None,
    handoff: bool,
) -> ConversationPlaybookCard:
    if handoff:
        return _CARDS["order_handoff"]
    if objection == "price":
        return _CARDS["price_objection"]
    card_id = {
        SupportIntent.PRODUCT_RECOMMENDATION: "recommendation",
        SupportIntent.PRODUCT_COMPARISON: "comparison",
        SupportIntent.PRODUCT_CARE: "care_storage",
        SupportIntent.PRIVACY_PACKAGING: "privacy_packaging",
        SupportIntent.SITE_TRUST: "site_trust",
        SupportIntent.SHIPPING_CUSTOMS: "shipping_customs",
        SupportIntent.PRODUCT_CUSTOMIZATION: "customization",
    }.get(intent, "general_support")
    return _CARDS[card_id]
