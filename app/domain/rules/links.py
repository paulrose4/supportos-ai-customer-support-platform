import re
from enum import StrEnum
from urllib.parse import urlparse

from app.domain.models import KnowledgeEvidence


class LinkDisplayIntent(StrEnum):
    NONE = "none"
    EXPLICIT = "explicit"
    PRODUCT_RECOMMENDATION = "product_recommendation"
    PRODUCT_TRANSACTION = "product_transaction"


_EXPLICIT_LINK_TERMS = (
    "link",
    "url",
    "product page",
    "show me the page",
    "send me",
    "链接",
    "网址",
    "商品页",
    "页面",
    "发给我",
    "リンク",
    "seite",
    "enlace",
    "lien",
)
_RECOMMENDATION_TERMS = (
    "recommend",
    "suggest",
    "which model",
    "which product",
    "best option",
    "show me products",
    "推荐",
    "哪一款",
    "哪个型号",
    "有什么产品",
    "适合我的",
    "おすすめ",
    "empfehlen",
    "recomendar",
    "conseiller",
)
_TRANSACTION_TERMS = (
    "buy",
    "purchase",
    "order this",
    "how much",
    "price",
    "in stock",
    "availability",
    "shipping",
    "delivery",
    "where can i get",
    "购买",
    "下单",
    "多少钱",
    "价格",
    "现货",
    "库存",
    "发货",
    "配送",
    "哪里可以买",
    "価格",
    "在庫",
    "配送",
    "kaufen",
    "preis",
    "versand",
    "comprar",
    "precio",
    "acheter",
    "prix",
)
_CARE_OR_EXPLANATION_TERMS = (
    "care",
    "clean",
    "maintain",
    "storage",
    "material",
    "difference",
    "保养",
    "清洁",
    "收纳",
    "材质",
    "区别",
    "お手入れ",
    "pflege",
    "limpiar",
    "entretien",
)
_IDENTIFIER_PATTERN = re.compile(
    r"\b(?=[A-Za-z0-9_-]{4,}\b)(?=[A-Za-z0-9_-]*[A-Za-z])"
    r"(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]+\b"
)


def classify_link_display_intent(message: str) -> LinkDisplayIntent:
    normalized = " ".join(message.casefold().split())
    if any(term in normalized for term in _EXPLICIT_LINK_TERMS):
        return LinkDisplayIntent.EXPLICIT
    if any(term in normalized for term in _CARE_OR_EXPLANATION_TERMS):
        return LinkDisplayIntent.NONE
    if any(term in normalized for term in _RECOMMENDATION_TERMS):
        return LinkDisplayIntent.PRODUCT_RECOMMENDATION
    if any(term in normalized for term in _TRANSACTION_TERMS):
        return LinkDisplayIntent.PRODUCT_TRANSACTION
    return LinkDisplayIntent.NONE


def select_customer_links(
    *,
    message: str,
    evidence: list[KnowledgeEvidence],
    page_path: str | None = None,
) -> tuple[str, ...]:
    intent = classify_link_display_intent(message)
    if intent is LinkDisplayIntent.NONE:
        return ()

    current_path = (page_path or "").strip()
    identifiers = {value.casefold() for value in _IDENTIFIER_PATTERN.findall(message)}
    candidates: list[str] = []
    for item in evidence:
        source = item.source.strip()
        if not source.startswith(("https://", "http://")):
            continue
        if current_path and current_path != "/" and urlparse(source).path == current_path:
            continue
        if intent is not LinkDisplayIntent.EXPLICIT and not _is_product_evidence(item):
            continue
        if identifiers and not _matches_identifier(item, identifiers):
            continue
        if source not in candidates:
            candidates.append(source)

    multi_link_intents = {
        LinkDisplayIntent.EXPLICIT,
        LinkDisplayIntent.PRODUCT_RECOMMENDATION,
    }
    limit = 3 if intent in multi_link_intents else 1
    return tuple(candidates[:limit])


def _is_product_evidence(evidence: KnowledgeEvidence) -> bool:
    product = evidence.metadata.get("product")
    return bool(product) or str(evidence.metadata.get("category") or "").casefold() in {
        "product",
        "product_detail",
    }


def _matches_identifier(evidence: KnowledgeEvidence, identifiers: set[str]) -> bool:
    searchable = f"{evidence.text}\n{evidence.metadata.get('product', {})}".casefold()
    return any(identifier in searchable for identifier in identifiers)
