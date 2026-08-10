from __future__ import annotations

from dataclasses import dataclass

MAX_HEADING_LENGTH = 240
MAX_PERSISTED_HEADING_LENGTH = 500
MAX_DOCUMENT_TITLE_LENGTH = 500
MAX_CHUNK_TYPE_LENGTH = 50
MAX_PARENT_CHUNK_ID_LENGTH = 100
MAX_SECTION_PATH_DEPTH = 8
MAX_SECTION_PATH_ITEM_LENGTH = 240
MAX_FACT_KEYS = 32
MAX_FACT_KEY_LENGTH = 80


@dataclass(frozen=True, slots=True)
class KnowledgeChunkMetadataOverflowError(ValueError):
    field: str
    actual_length: int
    max_length: int
    document_id: str | None = None
    url: str | None = None
    ordinal: int | None = None

    def __str__(self) -> str:
        context = []
        if self.document_id:
            context.append(f"document_id={self.document_id}")
        if self.url:
            context.append(f"url={self.url}")
        if self.ordinal is not None:
            context.append(f"ordinal={self.ordinal}")
        suffix = f" ({', '.join(context)})" if context else ""
        return (
            f"knowledge_chunk_{self.field}_too_long: {self.field} length "
            f"{self.actual_length} exceeds {self.max_length}{suffix}"
        )


def validate_chunk_metadata(
    *,
    heading: str | None,
    chunk_type: str,
    parent_chunk_id: str | None,
    section_path: tuple[str, ...],
    fact_keys: tuple[str, ...],
    document_id: str | None = None,
    url: str | None = None,
    ordinal: int | None = None,
) -> None:
    _validate_length("heading", heading, MAX_PERSISTED_HEADING_LENGTH, document_id, url, ordinal)
    _validate_length("chunk_type", chunk_type, MAX_CHUNK_TYPE_LENGTH, document_id, url, ordinal)
    _validate_length(
        "parent_chunk_id",
        parent_chunk_id,
        MAX_PARENT_CHUNK_ID_LENGTH,
        document_id,
        url,
        ordinal,
    )
    if len(section_path) > MAX_SECTION_PATH_DEPTH:
        raise KnowledgeChunkMetadataOverflowError(
            "section_path",
            len(section_path),
            MAX_SECTION_PATH_DEPTH,
            document_id,
            url,
            ordinal,
        )
    for item in section_path:
        _validate_length(
            "section_path_item",
            item,
            MAX_SECTION_PATH_ITEM_LENGTH,
            document_id,
            url,
            ordinal,
        )
    if len(fact_keys) > MAX_FACT_KEYS:
        raise KnowledgeChunkMetadataOverflowError(
            "fact_keys", len(fact_keys), MAX_FACT_KEYS, document_id, url, ordinal
        )
    for item in fact_keys:
        _validate_length("fact_key", item, MAX_FACT_KEY_LENGTH, document_id, url, ordinal)


def validate_document_metadata(
    *,
    title: str,
    document_id: str | None = None,
    url: str | None = None,
) -> None:
    _validate_length("document_title", title, MAX_DOCUMENT_TITLE_LENGTH, document_id, url, None)


def _validate_length(
    field: str,
    value: str | None,
    maximum: int,
    document_id: str | None,
    url: str | None,
    ordinal: int | None,
) -> None:
    if value is not None and len(value) > maximum:
        raise KnowledgeChunkMetadataOverflowError(
            field, len(value), maximum, document_id, url, ordinal
        )
