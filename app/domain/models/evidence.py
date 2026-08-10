from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class KnowledgeEvidence:
    chunk_id: str
    document_id: str
    text: str
    score: float
    source: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BusinessEvidence:
    source: str
    fetched_at: datetime
    version: str | None
    facts: dict[str, Any] = field(default_factory=dict)
