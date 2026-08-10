from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from app.domain.models import KnowledgeEvidence, ProductSnapshotActivation
from app.domain.ports.models import SparseEmbedding
from app.domain.rules.knowledge_metadata import validate_chunk_metadata, validate_document_metadata

GLOBAL_KNOWLEDGE_PARTITION = "__global__"


class KnowledgeVersionSchemaInvariantError(RuntimeError):
    """The database schema cannot safely materialize a knowledge version."""

    error_code = "knowledge_version_schema_invariant_violation"
    retryable = False
    manual_remediation = True


@dataclass(frozen=True, slots=True)
class KnowledgeQuery:
    tenant_id: str
    text: str
    audience: str
    language: str
    limit: int = 5
    score_threshold: float = 0.35
    strict_language: bool = False
    filters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class KnowledgeChunk:
    tenant_id: str
    chunk_id: str
    document_id: str
    version_id: str
    text: str
    vector: Sequence[float]
    sparse_vector: SparseEmbedding | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class KnowledgeChunkRecord:
    chunk_id: str
    content_hash: str
    sequence: int
    heading: str | None
    chunk_type: str = "general"
    parent_chunk_id: str | None = None
    section_path: tuple[str, ...] = ()
    fact_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        validate_chunk_metadata(
            heading=self.heading,
            chunk_type=self.chunk_type,
            parent_chunk_id=self.parent_chunk_id,
            section_path=self.section_path,
            fact_keys=self.fact_keys,
        )


@dataclass(frozen=True, slots=True)
class KnowledgeVersionDraft:
    version_id: str
    tenant_id: str
    document_id: str
    source_path: str
    title: str
    version: str
    content_hash: str
    status: str
    authority_level: int
    priority: int
    language: str
    audience: str
    effective_from: str | None
    effective_to: str | None
    metadata: dict[str, Any]
    internal_links: tuple[str, ...]

    def __post_init__(self) -> None:
        validate_document_metadata(
            title=self.title,
            document_id=self.document_id,
            url=self.source_path,
        )


@dataclass(frozen=True, slots=True)
class KnowledgeVersionSnapshot:
    version_id: str
    content_hash: str
    status: str
    index_status: str
    index_namespace: str | None = None
    lifecycle_state: str = "draft"


@dataclass(frozen=True, slots=True)
class ActiveWebDocumentSnapshot:
    document_id: str
    version_id: str
    canonical_url: str
    requested_url: str
    final_url: str
    etag: str | None = None
    last_modified: str | None = None
    product_key: str | None = None
    content_kind: str = "general"
    topics: tuple[str, ...] = ()
    activated_at: str | None = None


@dataclass(frozen=True, slots=True)
class ProductIdentifierConflict:
    identifier: str
    document_ids: tuple[str, ...]
    version_ids: tuple[str, ...]
    canonical_urls: tuple[str, ...]
    product_names: tuple[str, ...]
    brands: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class KnowledgeControlPlaneSiteAudit:
    tenant_id: str
    site_id: str
    baseline_publication_id: str | None
    baseline_version_ids: tuple[str, ...]
    baseline_missing_version_ids: tuple[str, ...]
    baseline_manifest_count: int
    current_version_ids: tuple[str, ...]
    current_manifest_count: int
    current_document_count: int
    invalid_current_pointer_count: int
    discarded_current_pointer_count: int
    failed_current_pointer_count: int
    orphan_current_pointer_count: int
    current_snapshot_ids: tuple[str, ...]
    active_product_snapshot_id: str | None
    active_product_snapshot_count: int
    active_product_count: int
    baseline_product_snapshot_status: str | None
    baseline_product_count: int
    identifier_conflicts: tuple[ProductIdentifierConflict, ...]
    # The site gate is part of the audit because a healthy projection is not
    # serveable while its control-plane state is switching or in recovery.
    publication_state: str = "active"
    publication_active_id: str | None = None
    publication_pending_id: str | None = None
    publication_error_code: str | None = None


@dataclass(frozen=True, slots=True)
class KnowledgeIndexSiteAudit:
    collection_exists: bool
    site_point_count: int
    active_point_count: int
    inactive_point_count: int
    target_point_count: int
    target_active_point_count: int
    active_version_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class KnowledgeSitePublicationState:
    state: str
    active_publication_id: str | None = None
    pending_publication_id: str | None = None
    error_code: str | None = None
    switch_origin_state: str | None = None


class KnowledgeRetrieverPort(Protocol):
    async def search(self, query: KnowledgeQuery) -> list[KnowledgeEvidence]: ...


class KnowledgeRerankerPort(Protocol):
    async def rerank(
        self,
        *,
        query: KnowledgeQuery,
        candidates: Sequence[KnowledgeEvidence],
        limit: int,
    ) -> list[KnowledgeEvidence]: ...


class KnowledgeIndexerPort(Protocol):
    async def initialize(self) -> None: ...

    async def upsert(self, chunks: Sequence[KnowledgeChunk]) -> None: ...

    async def delete_document(self, *, tenant_id: str, document_id: str) -> None: ...

    async def activate_document_version(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str | None,
    ) -> None: ...

    async def activate_site_versions(
        self,
        *,
        tenant_id: str,
        site_id: str,
        version_ids: Sequence[str],
        expected_point_count: int | None = None,
    ) -> None: ...

    async def discard_snapshot(
        self,
        *,
        tenant_id: str,
        site_id: str,
        snapshot_id: str,
    ) -> None: ...

    async def discard_staged_document(
        self,
        *,
        tenant_id: str,
        site_id: str,
        snapshot_id: str,
        document_id: str,
    ) -> None: ...

    async def count_snapshot_points(
        self,
        *,
        tenant_id: str,
        site_id: str,
        snapshot_id: str,
    ) -> int: ...

    async def audit_site_publication(
        self,
        *,
        tenant_id: str,
        site_id: str,
        target_version_ids: Sequence[str],
    ) -> KnowledgeIndexSiteAudit: ...


class KnowledgeControlPlanePort(Protocol):
    async def has_active_approved_care_sop(self, language: str | None = None) -> bool: ...

    async def list_active_site_web_documents(
        self,
        *,
        tenant_id: str,
        site_id: str,
    ) -> tuple[ActiveWebDocumentSnapshot, ...]: ...

    async def list_active_site_version_ids(
        self,
        *,
        tenant_id: str,
        site_id: str,
    ) -> tuple[str, ...]: ...

    async def list_related_document_ids(
        self,
        *,
        tenant_id: str,
        document_ids: Sequence[str],
        limit: int = 20,
    ) -> tuple[str, ...]: ...

    async def begin_sync(
        self,
        *,
        tenant_id: str,
        vault_path: str,
        actor_subject_id: str | None = None,
        correlation_id: str | None = None,
        approval_reference: str | None = None,
        sync_job_id: str | None = None,
    ) -> str: ...

    async def count_indexed_chunks(
        self,
        *,
        tenant_id: str,
        version_ids: Sequence[str],
    ) -> int: ...

    async def is_reusable_site_version(
        self,
        *,
        tenant_id: str,
        site_id: str,
        version_id: str,
        publication_id: str,
    ) -> bool: ...

    async def validate_site_publication_versions(
        self,
        *,
        tenant_id: str,
        site_id: str,
        publication_id: str,
        version_ids: Sequence[str],
    ) -> tuple[str, ...]: ...

    async def invalid_site_publication_version_ids(
        self,
        *,
        tenant_id: str,
        site_id: str,
        publication_id: str,
        version_ids: Sequence[str],
    ) -> tuple[str, ...]: ...

    async def audit_site_publication(
        self,
        *,
        tenant_id: str,
        site_id: str,
        baseline_publication_id: str | None = None,
    ) -> KnowledgeControlPlaneSiteAudit: ...

    async def list_publication_version_ids(
        self,
        *,
        tenant_id: str,
        site_id: str,
        publication_id: str,
    ) -> tuple[str, ...]: ...

    async def get_site_publication_state(
        self,
        *,
        tenant_id: str,
        site_id: str,
    ) -> KnowledgeSitePublicationState: ...

    async def begin_site_publication_switch(
        self,
        *,
        tenant_id: str,
        site_id: str,
        publication_id: str,
    ) -> KnowledgeSitePublicationState: ...

    async def complete_site_publication_switch(
        self,
        *,
        tenant_id: str,
        site_id: str,
        publication_id: str,
    ) -> None: ...

    async def restore_site_publication(
        self,
        *,
        tenant_id: str,
        site_id: str,
        failed_publication_id: str,
        previous_publication_id: str | None,
        error_code: str,
    ) -> None: ...

    async def require_site_publication_recovery(
        self,
        *,
        tenant_id: str,
        site_id: str,
        publication_id: str,
        error_code: str,
    ) -> None: ...

    async def discard_staged_document(
        self,
        *,
        tenant_id: str,
        site_id: str,
        snapshot_id: str,
        document_id: str,
    ) -> int: ...


class SitePublicationCommitPort(Protocol):
    async def commit_site_publication(
        self,
        *,
        tenant_id: str,
        site_id: str,
        publication_id: str,
        version_ids: Sequence[str],
        discovered_count: int,
        indexed_count: int,
        error_summary: dict[str, str],
        completed_at: datetime,
    ) -> ProductSnapshotActivation: ...


class KnowledgeSyncLeasePort(Protocol):
    async def acquire(self, *, tenant_id: str, ttl_seconds: int) -> str | None: ...

    async def release(self, *, tenant_id: str, token: str) -> None: ...

    async def get_latest_version(
        self, *, tenant_id: str, document_id: str
    ) -> KnowledgeVersionSnapshot | None: ...

    async def stage_version(
        self,
        *,
        draft: KnowledgeVersionDraft,
        chunks: Sequence[KnowledgeChunkRecord],
    ) -> str: ...

    async def mark_indexed(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
        point_ids: Sequence[str],
        index_namespace: str,
        activate: bool = True,
    ) -> None: ...

    async def activate_document_version(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str | None,
    ) -> None: ...

    async def activate_site_versions(
        self,
        *,
        tenant_id: str,
        site_id: str,
        version_ids: Sequence[str],
    ) -> None: ...

    async def mark_excluded(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
    ) -> None: ...

    async def record_ingestion_rejection(
        self,
        *,
        tenant_id: str,
        sync_job_id: str,
        source_path: str,
        reason_code: str,
    ) -> None: ...

    async def record_conflict(
        self,
        *,
        tenant_id: str,
        question: str,
        version_ids: Sequence[str],
        risk_level: int,
    ) -> str: ...

    async def complete_sync(
        self,
        *,
        tenant_id: str,
        sync_job_id: str,
        discovered_count: int,
        indexed_count: int,
        failed_count: int,
        error_summary: dict[str, str],
    ) -> None: ...

    async def fail_sync(
        self,
        *,
        tenant_id: str,
        sync_job_id: str,
        error_code: str,
    ) -> None: ...

    async def discard_site_snapshot_versions(
        self,
        *,
        tenant_id: str,
        site_id: str,
        snapshot_id: str,
    ) -> int: ...
