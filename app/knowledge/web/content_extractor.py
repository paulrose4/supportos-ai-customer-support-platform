import json
import re
import unicodedata
from datetime import datetime
from hashlib import sha256
from html import unescape
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from app.domain.rules.knowledge_metadata import MAX_DOCUMENT_TITLE_LENGTH, MAX_HEADING_LENGTH
from app.knowledge.web.errors import UnusableWebContentError
from app.knowledge.web.models import ParsedHtmlPage, StructuredWebDocument

_WHITESPACE = re.compile(r"\s+")
_TAG_PATTERN = re.compile(r"<[^>]{1,500}>")
_POLICY_TOPIC_TERMS: dict[str, tuple[str, ...]] = {
    # Keep the emitted topics stable across locales so readiness checks and
    # downstream retrieval do not need to understand translated labels.
    "delivery": (
        "shipping time",
        "delivery time",
        "shipping",
        "delivery",
        "versand",
        "versandkosten",
        "versandzeit",
        "lieferzeit",
        "lieferung",
        "zustellung",
        "versandbedingungen",
        "配送时间",
        "配送",
        "运输",
        "运费",
    ),
    "returns": (
        "return policy",
        "returns policy",
        "refund policy",
        "return",
        "returns",
        "refund",
        "rückgabe",
        "rückgaben",
        "rückgaberecht",
        "widerruf",
        "widerrufsrecht",
        "umtausch",
        "erstattung",
        "退货",
        "退换货",
        "退款",
    ),
    "privacy": (
        "privacy policy",
        "privacy",
        "datenschutz",
        "datenschutzerklärung",
        "privatsphäre",
        "隐私政策",
        "隐私",
    ),
    "payment": (
        "payment methods",
        "payment",
        "payments",
        "zahlung",
        "zahlungen",
        "zahlungsarten",
        "bezahlung",
        "klarna",
        "paypal",
        "支付",
        "付款",
    ),
    "warranty": (
        "warranty",
        "guarantee",
        "garantie",
        "gewährleistung",
        "sachmängelhaftung",
        "保修",
        "质保",
    ),
}
_POLICY_CONTENT_TOPICS = frozenset(
    {"policy", "delivery", "returns", "privacy", "payment", "warranty"}
)
_BOILERPLATE_TEXT = frozenset(
    {
        "accept cookies",
        "add to wishlist",
        "back to top",
        "close menu",
        "cookie policy",
        "follow us",
        "log in",
        "menu",
        "my account",
        "newsletter",
        "privacy preferences",
        "search",
        "share",
        "shopping cart",
        "sign in",
        "skip to content",
        "subscribe",
    }
)


class StructuredContentExtractor:
    def __init__(self, *, max_document_characters: int = 120_000) -> None:
        if max_document_characters < 1000:
            raise ValueError("max document characters must be at least 1000")
        self._max_document_characters = max_document_characters

    def extract(
        self,
        *,
        page: ParsedHtmlPage,
        tenant_id: str,
        site_id: str,
        fetched_at: datetime,
        product: dict[str, Any] | None,
        last_modified: str | None = None,
        etag: str | None = None,
        language_fallback: str = "en",
        response_bytes: int | None = None,
    ) -> StructuredWebDocument:
        product_name = _clean(str((product or {}).get("name") or ""))
        title = (
            _clean(page.title)
            or _clean(page.meta.get("og:title", ""))
            or _clean(page.meta.get("twitter:title", ""))
            or product_name
        )
        if not title:
            raise UnusableWebContentError("missing_title")
        document_title = title[:MAX_DOCUMENT_TITLE_LENGTH].rstrip()
        heading_rejected_count = 0
        accepted_heading_blocks: list[str] = []
        if len(title) <= MAX_HEADING_LENGTH:
            title_block = f"# {title}"
            sections: list[str] = [title_block]
            accepted_heading_blocks.append(title_block)
        else:
            sections = [title]
            heading_rejected_count += 1
        description = _clean(
            page.meta.get("description")
            or page.meta.get("og:description")
            or page.meta.get("twitter:description")
            or ""
        )
        if description and description.casefold() != title.casefold():
            sections.append(description)
        if product:
            product_section = _format_product(product)
            if product_section:
                sections.append("## Structured product data\n\n" + product_section)
        seen: set[str] = {title.casefold(), description.casefold()}
        for block in page.blocks:
            text = _clean(block.text)
            normalized = text.casefold()
            if not _is_relevant(text, normalized, block.kind) or normalized in seen:
                continue
            seen.add(normalized)
            if block.kind == "heading":
                level = block.heading_level or 2
                if level <= 3 and len(text) <= MAX_HEADING_LENGTH:
                    heading_block = f"{'#' * max(level, 1)} {text}"
                    sections.append(heading_block)
                    accepted_heading_blocks.append(heading_block)
                else:
                    sections.append(text)
                    heading_rejected_count += 1
            elif block.kind == "list_item":
                sections.append(f"- {text}")
            elif block.kind == "table_row":
                sections.append(f"Table row: {text}")
            else:
                sections.append(text)
        body = "\n\n".join(sections)
        body = body[: self._max_document_characters].rstrip()
        if len(body) < 80:
            raise UnusableWebContentError("insufficient_relevant_content")
        content_payload = {
            "body": body,
            "canonical_url": page.canonical_url,
            "product": product,
        }
        content_hash = sha256(
            json.dumps(content_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        document_id = str(uuid5(NAMESPACE_URL, f"{tenant_id}:{site_id}:{page.canonical_url}"))
        content_topics, freshness_class, max_age_seconds = _freshness_metadata(body, product)
        content_kind = (
            "product"
            if product
            else "policy"
            if _POLICY_CONTENT_TOPICS.intersection(content_topics)
            else page.content_kind
        )
        return StructuredWebDocument(
            tenant_id=tenant_id,
            site_id=site_id,
            document_id=document_id,
            canonical_url=page.canonical_url,
            title=document_title,
            language=(page.language or language_fallback).split("-")[0].casefold(),
            body=body,
            content_hash=content_hash,
            category=content_kind,
            product=product,
            internal_links=page.internal_links,
            fetched_at=fetched_at,
            source_metadata={
                "description": description,
                "last_modified": last_modified,
                "etag": etag,
                "requested_url": page.requested_url,
                "final_url": page.final_url,
                "source_type": "website_html",
                "fetched_at": fetched_at.isoformat(),
                "parsed_block_count": len(page.blocks),
                "body_characters": len(body),
                "response_bytes": response_bytes,
                "parser_profile": page.parser_profile,
                "selected_root": page.selected_root,
                "selected_text_characters": page.selected_text_characters,
                "title_source": page.title_source,
                "title_original_characters": len(title),
                "title_truncated_count": int(len(title) > MAX_DOCUMENT_TITLE_LENGTH),
                "heading_rejected_count": heading_rejected_count,
                "heading_truncated_count": 0,
                "content_kind": content_kind,
                "content_topics": list(content_topics),
                "freshness_class": freshness_class,
                "max_age_seconds": max_age_seconds,
            },
            heading_blocks=frozenset(accepted_heading_blocks),
        )


def _is_relevant(text: str, normalized: str, kind: str) -> bool:
    if not text or _TAG_PATTERN.search(text):
        return False
    if normalized in _BOILERPLATE_TEXT:
        return False
    if normalized.startswith("copyright ") or normalized.startswith("©"):
        return False
    if kind == "heading":
        return len(text) >= 2
    if kind in {"list_item", "table_row"}:
        return len(text) >= 3
    return len(text) >= 20 or any(character.isdigit() for character in text)


def _clean(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", unescape(value))
    normalized = "".join(
        " "
        if character.isspace()
        else ""
        if unicodedata.category(character).startswith("C")
        else character
        for character in normalized
    )
    return _WHITESPACE.sub(" ", normalized).strip()


def _format_product(product: dict[str, Any]) -> str:
    lines: list[str] = []
    labels = {
        "name": "Name",
        "sku": "SKU",
        "mpn": "MPN",
        "brand": "Brand",
        "category": "Category",
        "material": "Material",
        "price": "Price",
        "currency": "Currency",
        "description": "Description",
    }
    for key, label in labels.items():
        value = product.get(key)
        if isinstance(value, (str, int, float, bool)) and str(value).strip():
            lines.append(f"- {label}: {_clean(str(value))}")
    properties = product.get("properties")
    if isinstance(properties, dict):
        for key, value in list(properties.items())[:50]:
            lines.append(f"- {_clean(str(key))}: {_clean(str(value))}")
    offers = product.get("offers")
    if isinstance(offers, list):
        for index, offer in enumerate(offers[:20], start=1):
            if not isinstance(offer, dict):
                continue
            details = ", ".join(
                f"{key}={_clean(str(value))}"
                for key, value in offer.items()
                if isinstance(value, (str, int, float, bool))
            )
            if details:
                lines.append(f"- Offer {index}: {details}")
    return "\n".join(lines)


def _freshness_metadata(
    body: str,
    product: dict[str, Any] | None,
) -> tuple[tuple[str, ...], str, int]:
    normalized = body.casefold()
    topics: list[str] = []
    if product and (product.get("price") or product.get("offers")):
        topics.append("price")
    if "instock" in normalized or "in stock" in normalized or "库存" in normalized:
        topics.append("stock")
    for topic, terms in _POLICY_TOPIC_TERMS.items():
        if any(term in normalized for term in terms):
            topics.append(topic)
    if product:
        topics.append("product")
    topics = list(dict.fromkeys(topics))
    if any(topic in topics for topic in ("price", "stock")):
        return tuple(topics), "volatile", 900
    if "delivery" in topics:
        return tuple(topics), "operational", 7_200
    if any(topic in topics for topic in ("policy", "returns", "privacy", "payment", "warranty")):
        return tuple(topics), "policy", 604_800
    return tuple(topics or ["general"]), "catalog", 86_400
