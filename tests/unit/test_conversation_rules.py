from app.domain.models import ConversationTurn
from app.domain.rules import (
    product_reference_identifiers,
    product_reference_paths,
    resolve_reference_followup,
)


def test_url_only_message_inherits_previous_user_question() -> None:
    resolved = resolve_reference_followup(
        "https://shop.example.com/products/model.html?variant=1",
        (
            ConversationTurn(role="user", content="How should I care for a TPE product?"),
            ConversationTurn(role="assistant", content="Please send the product link."),
        ),
    )

    assert resolved.startswith("How should I care for a TPE product?")
    assert "PRODUCT_REFERENCE: https://shop.example.com/products/model.html?variant=1" in resolved


def test_standalone_question_does_not_inherit_previous_intent() -> None:
    assert (
        resolve_reference_followup(
            "Where can it be delivered?",
            (ConversationTurn(role="user", content="How should I clean it?"),),
        )
        == "Where can it be delivered?"
    )


def test_product_references_extract_paths_and_strong_skus() -> None:
    message = "this one https://shop.example.com/p/item.html?ref=chat SKU OVE01-21"

    assert product_reference_paths(message) == ("/p/item.html",)
    assert "OVE01-21" in product_reference_identifiers(message)
    assert product_reference_identifiers("I want a 100cm model") == ()
