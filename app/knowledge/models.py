from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.domain.rules.knowledge_metadata import validate_chunk_metadata


@dataclass(frozen=True, slots=True)
class ParsedKnowledgeDocument:
    path: Path
    metadata: dict[str, Any]
    body: str
    internal_links: tuple[str, ...]
    content_hash: str
    # None keeps legacy Markdown callers compatible; web extraction supplies
    # the exact parser-approved heading blocks to avoid prefix heuristics.
    heading_blocks: frozenset[str] | None = None


@dataclass(frozen=True, slots=True)
class TextChunk:
    chunk_id: str
    text: str
    sequence: int
    heading: str | None
    content_hash: str
    chunk_type: str = "general"
    parent_chunk_id: str | None = None
    section_path: tuple[str, ...] = ()
    fact_keys: tuple[str, ...] = ()
    context_text: str = ""

    def __post_init__(self) -> None:
        validate_chunk_metadata(
            heading=self.heading,
            chunk_type=self.chunk_type,
            parent_chunk_id=self.parent_chunk_id,
            section_path=self.section_path,
            fact_keys=self.fact_keys,
        )


@dataclass(frozen=True, slots=True)
class KnowledgeSyncReport:
    sync_job_id: str
    discovered_count: int
    indexed_count: int
    skipped_count: int
    excluded_count: int
    failed_count: int
    errors: dict[str, str]
