from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class KnowledgeStatus(StrEnum):
    DRAFT = "draft"
    REVIEW = "review"
    PUBLISHED = "published"
    ARCHIVED = "archived"


@dataclass(frozen=True, slots=True)
class KnowledgeDocument:
    document_id: str
    tenant_id: str
    source_path: str
    title: str
    status: KnowledgeStatus


@dataclass(frozen=True, slots=True)
class KnowledgeDocumentVersion:
    version_id: str
    document_id: str
    tenant_id: str
    version: str
    content_hash: str
    status: KnowledgeStatus
    effective_from: datetime | None
    effective_to: datetime | None


@dataclass(frozen=True, slots=True)
class RetrievalContext:
    active_product_sku: str | None = None
    pending_product_sku: str | None = None
    candidate_product_skus: tuple[str, ...] = ()
    country_code: str | None = None
    currency: str | None = None
    confirmed_fields: tuple[str, ...] = ()
    unresolved_question: str | None = None
    user_constraints: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RetrievalPlan:
    intent: str
    entities: tuple[str, ...]
    sources: tuple[str, ...]
    filters: dict[str, object] = field(default_factory=dict)
    query_variants: tuple[str, ...] = ()
    top_k: int = 5
    rerank_mode: str = "deterministic"
    latency_budget_ms: int = 800
    fallback_policy: str = "fail_closed"
