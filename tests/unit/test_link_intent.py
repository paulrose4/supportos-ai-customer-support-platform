from app.domain.models import KnowledgeEvidence
from app.domain.rules import (
    LinkDisplayIntent,
    classify_link_display_intent,
    select_customer_links,
)


def _product_evidence(
    *,
    source: str,
    sku: str,
    score: float = 0.9,
) -> KnowledgeEvidence:
    return KnowledgeEvidence(
        chunk_id=f"chunk-{sku}",
        document_id=f"product-{sku}",
        text=f"Model {sku} is available for purchase.",
        score=score,
        source=source,
        metadata={"category": "product", "product": {"sku": sku}},
    )


def test_care_and_material_questions_do_not_show_links() -> None:
    evidence = [_product_evidence(source="https://shop.example/p/sku-103", sku="SKU-103")]

    assert (
        classify_link_display_intent("How should I clean this TPE product?")
        is LinkDisplayIntent.NONE
    )
    assert classify_link_display_intent("TPE材质有什么特点？") is LinkDisplayIntent.NONE
    assert select_customer_links(message="如何保养？", evidence=evidence) == ()


def test_recommendation_returns_top_three_product_links() -> None:
    evidence = [
        _product_evidence(source=f"https://shop.example/products/model-{index}", sku=f"SKU-{index}")
        for index in range(1, 5)
    ]

    assert select_customer_links(message="请推荐几款适合我的产品", evidence=evidence) == (
        "https://shop.example/products/model-1",
        "https://shop.example/products/model-2",
        "https://shop.example/products/model-3",
    )


def test_transaction_returns_one_sku_matching_product_link() -> None:
    evidence = [
        _product_evidence(source="https://shop.example/products/other", sku="ABC10"),
        _product_evidence(source="https://shop.example/products/sku-103", sku="SKU-103"),
    ]

    assert select_customer_links(message="How much is SKU-103?", evidence=evidence) == (
        "https://shop.example/products/sku-103",
    )


def test_explicit_link_request_returns_relevant_links() -> None:
    evidence = [
        _product_evidence(source="https://shop.example/products/sku-103", sku="SKU-103"),
        KnowledgeEvidence(
            chunk_id="faq-shipping",
            document_id="faq",
            text="Shipping information",
            score=0.8,
            source="https://shop.example/shipping",
        ),
    ]

    assert select_customer_links(
        message="Please send me the product link for SKU-103",
        evidence=evidence,
    ) == ("https://shop.example/products/sku-103",)


def test_current_page_and_non_http_sources_are_excluded() -> None:
    evidence = [
        _product_evidence(source="https://shop.example/products/current", sku="CUR10"),
        _product_evidence(source="product.md", sku="CUR10"),
        _product_evidence(source="https://shop.example/products/other", sku="CUR10"),
    ]

    assert select_customer_links(
        message="What is the price of CUR10?",
        evidence=evidence,
        page_path="/products/current",
    ) == ("https://shop.example/products/other",)
