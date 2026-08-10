from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class WebSyncJobStatus(StrEnum):
    PREPARING = "preparing"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    CLEANUP_PENDING = "cleanup_pending"
    CANCELED = "canceled"


class WebSyncJobPhase(StrEnum):
    PREPARING = "preparing"
    QUEUED = "queued"
    PROCESSING = "processing"
    FINALIZING = "finalizing"
    AWAITING_REMEDIATION = "awaiting_remediation"
    COMPLETED = "completed"


class WebSyncJobItemStatus(StrEnum):
    PENDING = "pending"
    FETCHING = "fetching"
    SUCCEEDED = "succeeded"
    NOT_MODIFIED = "not_modified"
    EXCLUDED = "excluded"
    FAILED = "failed"
    CANCELED = "canceled"


class WebSyncTrigger(StrEnum):
    MANUAL = "manual"
    SCHEDULED = "scheduled"


class WebSyncMode(StrEnum):
    SHADOW = "shadow"
    PRODUCTION = "production"


class WebSyncPublicationStatus(StrEnum):
    NOT_REQUESTED = "not_requested"
    PENDING = "pending"
    PUBLISHED = "published"
    REFUSED = "refused"


@dataclass(frozen=True, slots=True)
class WebSyncValidatorSnapshot:
    document_id: str
    version_id: str
    canonical_url: str
    requested_url: str
    final_url: str
    etag: str | None = None
    last_modified: str | None = None
    product_key: str | None = None
    normalized_product_key: str | None = None
    normalization_version: str | None = None


@dataclass(frozen=True, slots=True)
class WebSyncPolicySnapshot:
    seed_urls: tuple[str, ...] = ()
    sitemap_urls: tuple[str, ...] = ()
    max_pages: int = 50_000
    max_sitemaps: int = 10
    max_response_bytes: int = 2_000_000
    max_decompressed_response_bytes: int = 4_000_000
    max_compression_ratio: float = 50.0
    request_timeout_seconds: float = 15.0
    crawl_delay_seconds: float = 0.25
    follow_internal_links: bool = True
    respect_robots_txt: bool = True
    batch_size: int = 250
    discover_sitemaps: bool = True
    primary_language: str = "en"
    translated_locales: tuple[str, ...] = ()
    manifest_fingerprint: str | None = None
    validators: tuple[WebSyncValidatorSnapshot, ...] = ()
    duplicate_product_policy: str = "first_wins"
    duplicate_product_order: str = "manifest_ordinal"


@dataclass(frozen=True, slots=True)
class WebSyncJobReport:
    pipeline_sync_job_id: str
    published: bool
    discovered_count: int
    document_count: int
    changed_document_count: int
    unchanged_document_count: int
    http_not_modified_count: int
    duplicate_count: int
    duplicate_product_count: int
    product_count: int
    pending_removal_count: int
    expired_count: int
    indexed_chunk_count: int
    excluded_count: int
    failed_count: int
    errors: dict[str, str] = field(default_factory=dict)
    duplicate_product_total: int = 0
    duplicate_product_excluded_count: int = 0
    duplicate_product_conflict_warning_count: int = 0
    duplicate_product_unresolved_count: int = 0
    winner_product_count: int = 0
    processed_page_count: int = 0
    produced_document_count: int = 0
    failed_page_count: int = 0
    unresolved_product_identity_count: int = 0
    blocking_issue_count: int = 0
    publication_block_reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WebSyncJobItem:
    tenant_id: str
    job_id: str
    site_id: str
    manifest_id: str
    item_id: str
    ordinal: int
    url: str
    source_sitemap_url: str
    content_kind: str = "general"
    status: WebSyncJobItemStatus = WebSyncJobItemStatus.PENDING
    attempt_count: int = 0
    max_attempts: int = 3
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    next_attempt_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    document_id: str | None = None
    version_id: str | None = None
    canonical_url: str | None = None
    final_url: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    product_key: str | None = None
    normalized_product_key: str | None = None
    normalization_version: str | None = None
    report: WebSyncJobReport | None = None
    error_code: str | None = None
    error_message: str | None = None
    outcome_reason: str | None = None
    winner_item_id: str | None = None
    winner_url: str | None = None
    identity_source: str | None = None
    policy_version: str | None = None


@dataclass(frozen=True, slots=True)
class WebSyncJob:
    tenant_id: str
    job_id: str
    site_id: str
    base_url: str
    status: WebSyncJobStatus
    trigger: WebSyncTrigger
    mode: WebSyncMode
    publication_status: WebSyncPublicationStatus
    manifest_id: str
    sample_size: int | None
    policy: WebSyncPolicySnapshot
    requested_by: str
    correlation_id: str
    idempotency_key: str
    requested_at: datetime
    manifest_version: int | None = None
    manifest_fingerprint: str | None = None
    prepared_count: int = 0
    phase: WebSyncJobPhase = WebSyncJobPhase.QUEUED
    expected_count: int = 0
    completed_count: int = 0
    succeeded_count: int = 0
    not_modified_count: int = 0
    excluded_item_count: int = 0
    failed_item_count: int = 0
    canceled_item_count: int = 0
    attempt_count: int = 0
    max_attempts: int = 3
    started_at: datetime | None = None
    heartbeat_at: datetime | None = None
    completed_at: datetime | None = None
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    blocked_at: datetime | None = None
    retention_expires_at: datetime | None = None
    report: WebSyncJobReport | None = None
    error_code: str | None = None
    error_message: str | None = None
    state_version: int = 1
    updated_at: datetime | None = None
    last_progress_at: datetime | None = None
    available_at: datetime | None = None
    prepare_stage: str = "copy_manifest_items"
    prepare_cursor: int = 0
    claim_count: int = 0
    failure_attempt_count: int = 0
    yield_count: int = 0
