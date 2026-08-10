import re
from hashlib import sha256
from uuid import NAMESPACE_URL, uuid5

from app.knowledge.models import ParsedKnowledgeDocument, TextChunk

_MARKDOWN_HEADING = re.compile(r"^#{1,6}\s+\S")


class MarkdownChunker:
    def __init__(self, max_characters: int = 1200) -> None:
        self._max_characters = max_characters

    def chunk(self, document: ParsedKnowledgeDocument) -> list[TextChunk]:
        blocks = [block.strip() for block in document.body.split("\n\n") if block.strip()]
        chunks: list[TextChunk] = []
        current: list[str] = []
        current_size = 0
        heading: str | None = None
        chunk_type = "general"

        def flush() -> None:
            nonlocal current, current_size
            if not current:
                return
            text = "\n\n".join(current)
            sequence = len(chunks)
            content_hash = sha256(text.encode("utf-8")).hexdigest()
            identity = f"{document.metadata['document_id']}:{sequence}:{chunk_type}:{content_hash}"
            chunk_id = str(uuid5(NAMESPACE_URL, identity))
            parent_chunk_id = str(
                uuid5(
                    NAMESPACE_URL,
                    f"{document.metadata['document_id']}:section:{heading or 'root'}",
                )
            )
            context_text = f"{heading}\n\n{text}" if heading and heading not in text else text
            chunks.append(
                TextChunk(
                    chunk_id,
                    text,
                    sequence,
                    heading,
                    content_hash,
                    chunk_type=chunk_type,
                    parent_chunk_id=parent_chunk_id,
                    section_path=(heading,) if heading else (),
                    fact_keys=_fact_keys(text),
                    context_text=context_text[:2400],
                )
            )
            current = []
            current_size = 0

        for block in blocks:
            is_heading = (
                block in document.heading_blocks
                if document.heading_blocks is not None
                else bool(_MARKDOWN_HEADING.match(block))
            )
            if is_heading:
                flush()
                heading = block.lstrip("#").strip()
            next_type = _chunk_type(block, heading, str(document.metadata.get("category") or ""))
            if current and next_type != chunk_type:
                flush()
            chunk_type = next_type
            limit = 2400 if chunk_type == "delivery_rule" else self._max_characters
            if current and current_size + len(block) > limit:
                flush()
            current.append(block)
            current_size += len(block)
        flush()
        return chunks


def _chunk_type(block: str, heading: str | None, category: str) -> str:
    normalized = f"{heading or ''} {block}".casefold()
    if any(
        term in normalized
        for term in (
            "processing time",
            "shipping time",
            "delivery",
            "versand",
            "versandkosten",
            "versandzeit",
            "lieferzeit",
            "lieferung",
            "zustellung",
            "versandbedingungen",
            "warehouse",
            "配送",
            "运输",
            "仓库",
        )
    ):
        return "delivery_rule"
    if any(
        term in normalized
        for term in (
            "return policy",
            "refund policy",
            "warranty",
            "rückgabe",
            "rückgaberecht",
            "widerruf",
            "umtausch",
            "erstattung",
            "datenschutz",
            "zahlung",
            "zahlungsarten",
            "garantie",
            "gewährleistung",
            "退货",
            "退款政策",
            "保修",
        )
    ):
        return "policy_clause"
    if any(term in normalized for term in ("clean", "care", "storage", "保养", "清洁", "收纳")):
        return "care_instruction"
    if any(
        term in normalized
        for term in ("price", "availability", "in stock", "offer", "价格", "库存", "现货")
    ):
        return "product_offer"
    if category.casefold() == "product":
        if any(term in normalized for term in ("sku", "name", "brand", "型号", "品牌")):
            return "product_identity"
        return "product_specification"
    return "general"


def _fact_keys(text: str) -> tuple[str, ...]:
    normalized = text.casefold()
    keys = []
    groups = {
        "processing_time": ("processing time", "处理时间"),
        "shipping_time": (
            "shipping time",
            "versandzeit",
            "lieferzeit",
            "运输时间",
            "配送时间",
        ),
        "price": ("price", "lowprice", "价格"),
        "stock": ("availability", "in stock", "库存", "现货"),
        "return_policy": (
            "return policy",
            "rückgabe",
            "rückgaberecht",
            "widerruf",
            "退货",
            "退换货",
        ),
        "care": ("clean", "care", "storage", "清洁", "保养", "收纳"),
    }
    for key, terms in groups.items():
        if any(term in normalized for term in terms):
            keys.append(key)
    return tuple(keys)
