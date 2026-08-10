import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from app.domain.models import ProductSnapshot
from app.domain.models.product_identity import ProductIdentity
from app.knowledge.web.models import StructuredWebDocument

_MONEY_AMOUNT = re.compile(
    r"(?i)(?:[$€£¥￥]\s*(\d+(?:,\d{3})*(?:\.\d{1,2})?)|"
    r"(\d+(?:,\d{3})*(?:\.\d{1,2})?)\s*(?:USD|EUR|GBP|CAD|AUD|JPY|CNY))"
)

_DIMENSION_TERMS = (
    "dimension",
    "height",
    "width",
    "length",
    "depth",
    "size",
    "尺寸",
    "高度",
    "宽度",
    "长度",
)
_WEIGHT_TERMS = ("weight", "net weight", "重量", "净重")
_WAREHOUSE_TERMS = ("warehouse", "ships from", "dispatch from", "发货仓", "仓库")
_REGION_TERMS = (
    "shipping region",
    "delivery area",
    "ships to",
    "delivery range",
    "配送范围",
    "配送地区",
)


def to_product_snapshot(
    document: StructuredWebDocument,
    *,
    snapshot_id: str,
) -> ProductSnapshot | None:
    product = document.product
    if not isinstance(product, dict):
        return None
    sku = _text(product.get("sku"))
    mpn = _text(product.get("mpn"))
    name = _text(product.get("name")) or document.title.strip()
    if not name or not (sku or mpn):
        return None
    properties = product.get("properties")
    normalized_properties = properties if isinstance(properties, Mapping) else {}
    offer = _first_offer(product.get("offers"))
    price = _text(product.get("price")) or _text(offer.get("price")) or _text(offer.get("lowPrice"))
    currency = _text(product.get("currency")) or _text(offer.get("priceCurrency"))
    raw_product_key = (sku or mpn or document.canonical_url).strip()
    identity = ProductIdentity.from_value(
        raw_product_key,
        source="sku" if sku else "mpn" if mpn else "canonical_url",
    )
    if identity is None:
        return None
    product_key = raw_product_key.upper()
    return ProductSnapshot(
        tenant_id=document.tenant_id,
        site_id=document.site_id,
        snapshot_id=snapshot_id,
        product_key=product_key,
        normalized_product_key=identity.normalized_key,
        normalization_version=identity.normalization_version,
        sku=sku.upper() if sku else None,
        mpn=mpn.upper() if mpn else None,
        name=name,
        canonical_url=document.canonical_url,
        brand=_text(product.get("brand")),
        material=_text(product.get("material"))
        or _property(normalized_properties, ("material", "材质")),
        attributes=_attributes(normalized_properties),
        dimensions=_dimensions(normalized_properties),
        weight=_text(product.get("weight")) or _property(normalized_properties, _WEIGHT_TERMS),
        price=price,
        currency=currency.upper() if currency else None,
        stock_status=_text(offer.get("availability")),
        shipping_warehouse=_text(product.get("shipping_warehouse"))
        or _property(normalized_properties, _WAREHOUSE_TERMS),
        shipping_regions=_regions(product.get("shipping_regions"))
        or _regions(_property(normalized_properties, _REGION_TERMS)),
        fetched_at=document.fetched_at,
        content_hash=document.content_hash,
        source_url=document.canonical_url,
        etag=_text(document.source_metadata.get("etag")),
        last_modified=_text(document.source_metadata.get("last_modified")),
    )


def _first_offer(value: Any) -> dict[str, Any]:
    if isinstance(value, list):
        return next((item for item in value if isinstance(item, dict)), {})
    return value if isinstance(value, dict) else {}


def product_price_conflict(document: StructuredWebDocument) -> tuple[str, ...]:
    product = document.product
    if not isinstance(product, dict):
        return ()
    offer = _first_offer(product.get("offers"))
    structured_values = {
        value
        for raw in (product.get("price"), offer.get("price"), offer.get("lowPrice"))
        if (value := _normalized_amount(raw)) is not None
    }
    if not structured_values:
        return ()
    prose = document.body.split("## Structured product data", 1)[0]
    prose_values = {
        value
        for match in _MONEY_AMOUNT.finditer(prose)
        if (value := _normalized_amount(match.group(1) or match.group(2))) is not None
    }
    if not prose_values or prose_values & structured_values:
        return ()
    return tuple(sorted(prose_values | structured_values))


def _dimensions(properties: Mapping[object, object]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in properties.items():
        label = str(key).strip()
        normalized = label.casefold()
        if any(term in normalized for term in _DIMENSION_TERMS):
            text = _text(value)
            if text:
                result[label[:100]] = text[:200]
    return result


def _attributes(properties: Mapping[object, object]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in properties.items():
        label = str(key).strip()
        text = _text(value)
        if label and text:
            result[label[:100]] = text[:500]
        if len(result) >= 100:
            break
    return result


def _property(properties: Mapping[object, object], terms: tuple[str, ...]) -> str | None:
    for key, value in properties.items():
        normalized = str(key).strip().casefold()
        if any(term in normalized for term in terms):
            text = _text(value)
            if text:
                return text
    return None


def _regions(value: object) -> tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))[:50]
    parts = re.split(r"[,;|、，]+", str(value))
    return tuple(dict.fromkeys(part.strip() for part in parts if part.strip()))[:50]


def _text(value: object) -> str | None:
    if isinstance(value, (str, int, float)) and str(value).strip():
        return str(value).strip()
    return None


def _normalized_amount(value: object) -> str | None:
    text = _text(value)
    if not text:
        return None
    match = re.search(r"\d+(?:\.\d+)?", text.replace(",", ""))
    if match is None:
        return None
    try:
        normalized = format(Decimal(match.group(0)), "f")
    except InvalidOperation:
        return None
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized
