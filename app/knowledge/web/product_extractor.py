from collections.abc import Iterable
from typing import Any

from app.knowledge.web.models import ParsedHtmlPage


class ProductExtractor:
    def extract(self, page: ParsedHtmlPage) -> dict[str, Any] | None:
        candidates = [item for item in _walk_objects(page.structured_data) if _is_product(item)]
        product = candidates[0] if candidates else {}
        result: dict[str, Any] = {}
        _copy_scalar(product, result, "name")
        _copy_scalar(product, result, "sku")
        _copy_scalar(product, result, "mpn")
        _copy_scalar(product, result, "description")
        _copy_scalar(product, result, "category")
        _copy_scalar(product, result, "material")
        brand = product.get("brand")
        if isinstance(brand, dict):
            _copy_scalar(brand, result, "name", target="brand")
        elif isinstance(brand, str) and brand.strip():
            result["brand"] = brand.strip()
        offers = product.get("offers")
        offer_items = offers if isinstance(offers, list) else [offers]
        normalized_offers = [
            _normalize_offer(item) for item in offer_items if isinstance(item, dict)
        ]
        normalized_offers = [item for item in normalized_offers if item]
        if normalized_offers:
            result["offers"] = normalized_offers[:20]
        warehouse, shipping_regions = _shipping_facts(offer_items)
        if warehouse:
            result["shipping_warehouse"] = warehouse
        if shipping_regions:
            result["shipping_regions"] = shipping_regions
        properties = _additional_properties(product.get("additionalProperty"))
        for key, label in (
            ("height", "Height"),
            ("width", "Width"),
            ("depth", "Depth"),
            ("length", "Length"),
            ("weight", "Weight"),
        ):
            value = _quantity(product.get(key))
            if value:
                properties.setdefault(label, value)
        for label, value in _page_properties(page).items():
            properties.setdefault(label, value)
        if properties:
            result["properties"] = properties
        images = product.get("image")
        if isinstance(images, str):
            result["image_count"] = 1
        elif isinstance(images, list):
            result["image_count"] = min(len(images), 1000)

        meta_fallbacks = {
            "name": ("og:title", "twitter:title"),
            "description": ("description", "og:description", "twitter:description"),
            "brand": ("product:brand",),
            "price": ("product:price:amount",),
            "currency": ("product:price:currency",),
        }
        for target, keys in meta_fallbacks.items():
            if target in result:
                continue
            for key in keys:
                value = page.meta.get(key)
                if value:
                    result[target] = value
                    break
        if not result.get("name") and page.title:
            result["name"] = page.title
        product_signals = {"sku", "mpn", "offers", "price", "currency", "brand", "properties"}
        if not product or not (product_signals & result.keys()):
            return None
        return result


def _walk_objects(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        graph = value.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                yield from _walk_objects(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk_objects(item)


def _is_product(value: dict[str, Any]) -> bool:
    item_type = value.get("@type")
    if isinstance(item_type, str):
        return item_type.casefold() == "product"
    if isinstance(item_type, list):
        return any(isinstance(item, str) and item.casefold() == "product" for item in item_type)
    return False


def _copy_scalar(
    source: dict[str, Any],
    target_map: dict[str, Any],
    source_key: str,
    *,
    target: str | None = None,
) -> None:
    value = source.get(source_key)
    if isinstance(value, (str, int, float, bool)) and str(value).strip():
        target_map[target or source_key] = value.strip() if isinstance(value, str) else value


def _normalize_offer(value: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in (
        "price",
        "lowPrice",
        "highPrice",
        "priceCurrency",
        "availability",
        "itemCondition",
        "url",
        "sku",
    ):
        item = value.get(key)
        if isinstance(item, (str, int, float, bool)) and str(item).strip():
            result[key] = item.strip() if isinstance(item, str) else item
    return result


def _additional_properties(value: Any) -> dict[str, str]:
    items = value if isinstance(value, list) else [value]
    result: dict[str, str] = {}
    for item in items[:100]:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        property_value = item.get("value")
        if isinstance(name, str) and isinstance(property_value, (str, int, float, bool)):
            normalized_name = name.strip()
            normalized_value = str(property_value).strip()
            if normalized_name and normalized_value:
                result[normalized_name[:200]] = normalized_value[:1000]
        if len(result) >= 50:
            break
    return result


def _page_properties(page: ParsedHtmlPage) -> dict[str, str]:
    result: dict[str, str] = {}
    for block in page.blocks:
        if block.kind != "table_row" or " | " not in block.text:
            continue
        label, value = (part.strip() for part in block.text.split(" | ", 1))
        if label and value and len(label) <= 100 and len(value) <= 300:
            result[label] = value
    return result


def _quantity(value: Any) -> str | None:
    if isinstance(value, (str, int, float)) and str(value).strip():
        return str(value).strip()
    if not isinstance(value, dict):
        return None
    amount = value.get("value") or value.get("minValue")
    unit = value.get("unitText") or value.get("unitCode")
    if not isinstance(amount, (str, int, float)) or not str(amount).strip():
        return None
    return " ".join(str(item).strip() for item in (amount, unit) if item not in (None, ""))


def _shipping_facts(offers: list[Any]) -> tuple[str | None, list[str]]:
    warehouse = None
    regions: list[str] = []
    for offer in offers[:20]:
        if not isinstance(offer, dict):
            continue
        details_value = offer.get("shippingDetails")
        details = details_value if isinstance(details_value, list) else [details_value]
        for item in details[:20]:
            if not isinstance(item, dict):
                continue
            warehouse = warehouse or _place_label(item.get("shippingOrigin"))
            destination = item.get("shippingDestination")
            destinations = destination if isinstance(destination, list) else [destination]
            for candidate in destinations:
                label = _place_label(candidate)
                if label and label not in regions:
                    regions.append(label)
    return warehouse, regions[:50]


def _place_label(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if not isinstance(value, dict):
        return None
    for key in ("name", "addressCountry", "addressRegion"):
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return item.strip()
    address = value.get("address")
    return _place_label(address)
