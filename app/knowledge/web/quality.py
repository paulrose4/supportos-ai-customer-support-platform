from dataclasses import dataclass

from app.knowledge.web.models import StructuredWebDocument


@dataclass(frozen=True, slots=True)
class WebDocumentQualityDecision:
    accepted: bool
    reason_codes: tuple[str, ...]
    content_kind: str
    body_characters: int
    block_count: int
    extraction_ratio: float | None


class WebDocumentQualityGate:
    def __init__(
        self,
        *,
        minimum_product_characters: int = 200,
        minimum_page_characters: int = 120,
        minimum_guide_characters: int = 800,
        minimum_blocks: int = 2,
        minimum_guide_blocks: int = 6,
        minimum_extraction_ratio: float = 0.003,
        minimum_product_extraction_ratio: float = 0.0005,
        minimum_guide_extraction_ratio: float = 0.25,
    ) -> None:
        self._minimum_product_characters = minimum_product_characters
        self._minimum_page_characters = minimum_page_characters
        self._minimum_guide_characters = minimum_guide_characters
        self._minimum_blocks = minimum_blocks
        self._minimum_guide_blocks = minimum_guide_blocks
        self._minimum_extraction_ratio = minimum_extraction_ratio
        self._minimum_product_extraction_ratio = minimum_product_extraction_ratio
        self._minimum_guide_extraction_ratio = minimum_guide_extraction_ratio

    def evaluate(self, document: StructuredWebDocument) -> WebDocumentQualityDecision:
        body_characters = len(document.body.strip())
        content_kind = str(document.source_metadata.get("content_kind") or document.category)
        block_count = int(document.source_metadata.get("parsed_block_count") or 0)
        selected_text_characters = int(
            document.source_metadata.get("selected_text_characters")
            or document.source_metadata.get("response_bytes")
            or 0
        )
        extraction_ratio = (
            min(1.0, body_characters / selected_text_characters)
            if selected_text_characters
            else None
        )
        reasons: list[str] = []
        minimum_characters = {
            "product": self._minimum_product_characters,
            "guide": self._minimum_guide_characters,
        }.get(content_kind, self._minimum_page_characters)
        minimum_blocks = (
            self._minimum_guide_blocks if content_kind == "guide" else self._minimum_blocks
        )
        minimum_ratio = (
            self._minimum_guide_extraction_ratio
            if content_kind == "guide"
            else self._minimum_product_extraction_ratio
            if content_kind == "product"
            else self._minimum_extraction_ratio
        )
        if body_characters < minimum_characters:
            reasons.append("body_too_short")
        if block_count and block_count < minimum_blocks:
            reasons.append("insufficient_content_blocks")
        if extraction_ratio is not None and extraction_ratio < minimum_ratio:
            reasons.append("extraction_ratio_too_low")
        if content_kind == "guide" and str(document.source_metadata.get("selected_root") or "") in {
            "body",
            "body_fallback",
            "document_fallback",
        }:
            reasons.append("guide_content_root_missing")
        if content_kind == "product":
            product = document.product or {}
            if not str(product.get("name") or "").strip():
                reasons.append("product_name_missing")
            if not str(product.get("sku") or product.get("mpn") or "").strip():
                reasons.append("product_identifier_missing")
        return WebDocumentQualityDecision(
            accepted=not reasons,
            reason_codes=tuple(reasons),
            content_kind=content_kind,
            body_characters=body_characters,
            block_count=block_count,
            extraction_ratio=extraction_ratio,
        )
