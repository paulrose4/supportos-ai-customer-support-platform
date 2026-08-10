from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.application.services import ProductCatalogAnswerService
from app.domain.models import EvidenceNeed, ProductSnapshot, TurnPlan, TurnSubQuestion
from app.domain.rules import advance_missing_product_status, product_identity_conflicts
from tests.fakes.adapters import InMemoryProductCatalog


def product(*, fetched_at: datetime | None = None) -> ProductSnapshot:
    return ProductSnapshot(
        tenant_id="tenant-a",
        site_id="site-a",
        snapshot_id="snapshot-1",
        product_key="SKU-100",
        sku="SKU-100",
        mpn="MPN-100",
        name="Example Product",
        canonical_url="https://shop.example.com/products/example.html",
        brand="Example Brand",
        material="TPE",
        dimensions={"Height": "125 cm"},
        weight="20 kg",
        price="579",
        currency="USD",
        stock_status="https://schema.org/InStock",
        shipping_regions=("United States", "Canada"),
        fetched_at=fetched_at or datetime(2026, 7, 27, tzinfo=UTC),
        content_hash="hash-1",
        source_url="https://shop.example.com/products/example.html",
    )


def test_product_identity_conflict_detects_sku_reuse_across_product_pages() -> None:
    existing = product()
    candidate = replace(
        existing,
        canonical_url="https://shop.example.com/products/other.html",
        source_url="https://shop.example.com/products/other.html",
        name="Different Product",
        dimensions={"Height": "60 cm"},
        content_hash="hash-2",
    )

    assert product_identity_conflicts(existing, candidate) == (
        "canonical_url",
        "name",
        "dimensions",
    )


async def test_staged_catalog_refuses_conflicting_product_identity() -> None:
    existing = product()
    catalog = InMemoryProductCatalog()
    await catalog.stage_products(
        tenant_id=existing.tenant_id,
        site_id=existing.site_id,
        snapshot_id=existing.snapshot_id,
        products=(existing,),
    )

    with pytest.raises(ValueError, match="product_identity_conflict:SKU-100"):
        await catalog.stage_products(
            tenant_id=existing.tenant_id,
            site_id=existing.site_id,
            snapshot_id=existing.snapshot_id,
            products=(
                replace(
                    existing,
                    canonical_url="https://shop.example.com/products/other.html",
                    source_url="https://shop.example.com/products/other.html",
                    dimensions={"Height": "60 cm"},
                ),
            ),
        )


async def test_staged_catalog_replaces_content_hash_for_same_product_url() -> None:
    existing = product()
    catalog = InMemoryProductCatalog()
    await catalog.stage_products(
        tenant_id=existing.tenant_id,
        site_id=existing.site_id,
        snapshot_id=existing.snapshot_id,
        products=(existing,),
    )

    refreshed = replace(
        existing,
        content_hash="hash-2",
        fetched_at=datetime(2026, 8, 7, tzinfo=UTC),
    )
    await catalog.stage_products(
        tenant_id=existing.tenant_id,
        site_id=existing.site_id,
        snapshot_id=existing.snapshot_id,
        products=(refreshed,),
    )

    stored = catalog.products[
        (existing.tenant_id, existing.site_id, existing.snapshot_id, existing.product_key)
    ]
    assert stored.content_hash == "hash-2"


async def test_price_answer_uses_exact_snapshot_date_without_inline_markdown() -> None:
    service = ProductCatalogAnswerService(InMemoryProductCatalog((product(),)))

    result = await service.execute(
        tenant_id="tenant-a",
        site_id="site-a",
        question="SKU-100 多少钱？",
        page_path="/",
        language="zh",
        now=datetime(2026, 7, 27, tzinfo=UTC),
    )

    assert result.status == "sufficient"
    assert result.message is not None
    assert "2026-07-27" in result.message
    assert "$579" in result.message
    assert "[" not in result.message
    assert result.citations[0].startswith("https://shop.example.com/products/example.html#")


async def test_stock_answer_is_explicitly_historical_not_realtime() -> None:
    service = ProductCatalogAnswerService(InMemoryProductCatalog((product(),)))

    result = await service.execute(
        tenant_id="tenant-a",
        site_id="site-a",
        question="SKU-100 现在有货吗？",
        page_path="/",
        language="zh",
        now=datetime(2026, 7, 27, tzinfo=UTC),
    )

    assert result.message is not None
    assert "最近一次核对" in result.message
    assert "有货" in result.message
    assert "保证有货" not in result.message


async def test_material_answer_is_concise_and_keeps_link_out_of_message() -> None:
    service = ProductCatalogAnswerService(InMemoryProductCatalog((product(),)))

    result = await service.execute(
        tenant_id="tenant-a",
        site_id="site-a",
        question="What material is this product made from?",
        page_path="/products/example.html",
        language="en",
        now=datetime(2026, 7, 27, tzinfo=UTC),
    )

    assert result.message == "This product is made from TPE."
    assert "http" not in result.message
    assert result.citations


async def test_current_page_query_string_is_removed_before_exact_lookup() -> None:
    service = ProductCatalogAnswerService(InMemoryProductCatalog((product(),)))

    result = await service.execute(
        tenant_id="tenant-a",
        site_id="site-a",
        question="What material is this product made from?",
        page_path="/products/example.html?121211",
        language="en",
        now=datetime(2026, 7, 27, tzinfo=UTC),
    )

    assert result.status == "sufficient"
    assert result.product is not None
    assert result.product.sku == "SKU-100"


async def test_ai_planned_product_turn_returns_all_field_level_facts() -> None:
    service = ProductCatalogAnswerService(InMemoryProductCatalog((product(),)))
    plan = TurnPlan(
        primary_goal="Confirm the product before purchase",
        sub_questions=(
            TurnSubQuestion("q1", "What is the price?", ("need-price",), 1),
            TurnSubQuestion("q2", "Is it in stock?", ("need-stock",), 2),
            TurnSubQuestion("q3", "Who makes it?", ("need-brand",), 3),
        ),
        evidence_needs=(
            EvidenceNeed(
                "need-price",
                "Establish the current listed price",
                "current_page_product",
                "product_snapshot_lookup",
            ),
            EvidenceNeed(
                "need-stock",
                "Establish current availability",
                "current_page_product",
                "product_snapshot_lookup",
            ),
            EvidenceNeed(
                "need-brand",
                "Identify the manufacturer",
                "current_page_product",
                "product_snapshot_lookup",
            ),
        ),
        target_entities=("current_page_product",),
    )

    result = await service.execute(
        tenant_id="tenant-a",
        site_id="site-a",
        question="How much is this, is it in stock, and who makes it?",
        page_path="/products/example.html",
        language="en",
        turn_plan=plan,
        now=datetime(2026, 7, 27, tzinfo=UTC),
    )

    assert result.status == "planned_context"
    field_facts = result.evidence[0].metadata["field_facts"]
    facts_by_predicate = {item["predicate"]: item for item in field_facts}
    assert facts_by_predicate["price"]["value"] == "579 USD"
    assert facts_by_predicate["stock_status"]["value"] == "InStock"
    assert facts_by_predicate["snapshot_checked_at"]["value"] == "2026-07-27"
    assert facts_by_predicate["brand"]["value"] == "Example Brand"
    assert facts_by_predicate["material"]["value"] == "TPE"
    assert facts_by_predicate["sku"]["value"] == "SKU-100"
    assert len({item["fact_id"] for item in field_facts}) == len(field_facts)


async def test_height_question_returns_only_height_from_multiple_dimensions() -> None:
    detailed = replace(
        product(),
        dimensions={
            "Height": "108cm / 42.52in",
            "Bra Size": "A Cup",
            "Oral Depth": "10cm / 3.94in",
            "Package Size": "98 x 32 x 26cm",
        },
    )
    service = ProductCatalogAnswerService(InMemoryProductCatalog((detailed,)))

    result = await service.execute(
        tenant_id="tenant-a",
        site_id="site-a",
        question="What is the height of this product?",
        page_path="/products/example.html",
        language="en",
        now=datetime(2026, 7, 27, tzinfo=UTC),
    )

    assert result.message == "This product is 108 cm (42.52 in) tall."
    assert "Bra Size" not in result.message
    assert "Package Size" not in result.message


async def test_us_stock_listing_answers_when_explicit_stock_status_is_missing() -> None:
    us_stock = replace(
        product(),
        name="AeroLite Camera Drone US Stock",
        canonical_url=("https://shop.example.com/aerolite-camera-drone-us-stock.html"),
        source_url=("https://shop.example.com/aerolite-camera-drone-us-stock.html"),
        stock_status=None,
    )
    service = ProductCatalogAnswerService(InMemoryProductCatalog((us_stock,)))

    result = await service.execute(
        tenant_id="tenant-a",
        site_id="site-a",
        question=(
            "Is this item available in US stock?\nPRODUCT_REFERENCE: "
            f"{us_stock.canonical_url}?121211"
        ),
        page_path="/",
        language="en",
        now=datetime(2026, 7, 27, tzinfo=UTC),
    )

    assert result.status == "sufficient"
    assert result.message is not None
    assert result.message.startswith("Yes.")
    assert "listed as US stock" in result.message
    assert "?121211" not in result.message


async def test_stale_price_does_not_repeat_numeric_value() -> None:
    stale = replace(product(), fetched_at=datetime.now(UTC) - timedelta(days=8))
    service = ProductCatalogAnswerService(InMemoryProductCatalog((stale,)))

    result = await service.execute(
        tenant_id="tenant-a",
        site_id="site-a",
        question="SKU-100 price",
        page_path="/",
        language="en",
    )

    assert result.status == "stale"
    assert result.message is not None
    assert "579" not in result.message


async def test_unknown_explicit_sku_never_substitutes_similar_product() -> None:
    service = ProductCatalogAnswerService(InMemoryProductCatalog((product(),)))

    result = await service.execute(
        tenant_id="tenant-a",
        site_id="site-a",
        question="What is the price of SKU-999?",
        page_path="/",
        language="en",
    )

    assert result.status == "product_not_found"
    assert result.product is None


async def test_unknown_explicit_sku_does_not_fall_back_to_current_page_product() -> None:
    service = ProductCatalogAnswerService(InMemoryProductCatalog((product(),)))

    result = await service.execute(
        tenant_id="tenant-a",
        site_id="site-a",
        question="What is the price of SKU-999?",
        page_path="/products/example.html?tracking=1",
        language="en",
    )

    assert result.status == "product_not_found"
    assert result.product is None


async def test_discount_code_is_never_confirmed_as_current() -> None:
    service = ProductCatalogAnswerService(InMemoryProductCatalog())

    result = await service.execute(
        tenant_id="tenant-a",
        site_id="site-a",
        question="优惠码 SAVE20 还能用吗？",
        page_path="/",
        language="zh",
    )

    assert result.status == "general_guidance"
    assert result.message is not None
    assert "不能确认" in result.message
    assert "结账页" in result.message


def test_missing_product_requires_two_successful_absences_before_expiry() -> None:
    first_status, first_count = advance_missing_product_status(current_missing_count=0)
    second_status, second_count = advance_missing_product_status(current_missing_count=first_count)

    assert first_status.value == "pending_removal"
    assert first_count == 1
    assert second_status.value == "expired"
    assert second_count == 2


async def test_recommendation_candidates_are_filtered_and_ranked_from_snapshots() -> None:
    lightweight = replace(
        product(),
        product_key="SKU-200",
        sku="SKU-200",
        mpn="MPN-200",
        name="Light Product",
        canonical_url="https://shop.example.com/products/light.html",
        source_url="https://shop.example.com/products/light.html",
        price="279",
        weight="13 kg",
    )
    service = ProductCatalogAnswerService(InMemoryProductCatalog((product(), lightweight)))

    result = await service.execute(
        tenant_id="tenant-a",
        site_id="site-a",
        question="Recommend a lightweight model under $300",
        page_path="/",
        language="en",
        now=datetime(2026, 7, 27, tzinfo=UTC),
    )

    assert result.status == "catalog_context"
    assert [item.sku for item in result.products] == ["SKU-200"]
    assert "279" in result.evidence[0].text
    recommendation = result.evidence[0].metadata["recommendation"]
    assert "within_budget" in recommendation["match_reasons"]
    assert "lightweight_option" in recommendation["match_reasons"]
    assert recommendation["missing_facts"] == []


async def test_relative_recommendation_uses_the_resolved_candidate_reference() -> None:
    cheaper = replace(
        product(),
        product_key="D03055",
        sku="D03055",
        mpn="D03055",
        name="Cheaper Product",
        canonical_url="https://shop.example.com/products/cheaper.html",
        source_url="https://shop.example.com/products/cheaper.html",
        price="249",
        weight="22 kg",
    )
    service = ProductCatalogAnswerService(InMemoryProductCatalog((product(), cheaper)))

    result = await service.execute(
        tenant_id="tenant-a",
        site_id="site-a",
        question="便宜一点的呢？\nPRODUCT_REFERENCE: SKU-100",
        page_path="/",
        language="zh",
        now=datetime(2026, 7, 27, tzinfo=UTC),
    )

    assert result.status == "catalog_context"
    assert [item.sku for item in result.products] == ["D03055"]
    recommendation = result.evidence[0].metadata["recommendation"]
    assert recommendation["mode"] == "substitute"
    assert "cheaper_than_source" in recommendation["match_reasons"]


async def test_recommendation_returns_at_most_three_explainable_candidates() -> None:
    products = tuple(
        replace(
            product(),
            product_key=f"SKU-{index}",
            sku=f"SKU-{index}",
            mpn=f"MPN-{index}",
            name=f"Option {index}",
            canonical_url=f"https://shop.example.com/products/{index}.html",
            source_url=f"https://shop.example.com/products/{index}.html",
            price=str(200 + index),
            weight=None if index == 4 else f"{10 + index} kg",
        )
        for index in range(1, 6)
    )
    service = ProductCatalogAnswerService(InMemoryProductCatalog(products))

    result = await service.execute(
        tenant_id="tenant-a",
        site_id="site-a",
        question="Recommend a lightweight TPE model under $500",
        page_path="/",
        language="en",
        now=datetime(2026, 7, 27, tzinfo=UTC),
    )

    assert len(result.products) == 3
    assert [item.sku for item in result.products] == ["SKU-1", "SKU-2", "SKU-3"]
    assert all("recommendation" in item.metadata for item in result.evidence)


async def test_recommendation_searches_beyond_first_two_hundred_catalog_rows() -> None:
    products = tuple(
        replace(
            product(),
            product_key=f"SKU-{index:04d}",
            sku=f"SKU-{index:04d}",
            mpn=f"MPN-{index:04d}",
            name=f"Option {index}",
            canonical_url=f"https://shop.example.com/products/{index}.html",
            source_url=f"https://shop.example.com/products/{index}.html",
            price="250" if index == 200 else "500",
        )
        for index in range(201)
    )
    service = ProductCatalogAnswerService(InMemoryProductCatalog(products))

    result = await service.execute(
        tenant_id="tenant-a",
        site_id="site-a",
        question="Recommend a TPE model under $300",
        page_path="/",
        language="en",
        now=datetime(2026, 7, 27, tzinfo=UTC),
    )

    assert result.status == "catalog_context"
    assert [item.sku for item in result.products] == ["SKU-0200"]


async def test_comparison_requires_every_exact_product_reference() -> None:
    service = ProductCatalogAnswerService(InMemoryProductCatalog((product(),)))

    result = await service.execute(
        tenant_id="tenant-a",
        site_id="site-a",
        question="Compare SKU-100 and SKU-999",
        page_path="/",
        language="en",
        now=datetime(2026, 7, 27, tzinfo=UTC),
    )

    assert result.status == "product_not_found"


async def test_policy_question_on_product_page_does_not_narrow_to_product_document() -> None:
    service = ProductCatalogAnswerService(InMemoryProductCatalog((product(),)))

    result = await service.execute(
        tenant_id="tenant-a",
        site_id="site-a",
        question="What is the return policy?",
        page_path="/products/example.html",
        language="en",
    )

    assert result.status == "not_applicable"
