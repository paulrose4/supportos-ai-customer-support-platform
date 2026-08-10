from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class WebKnowledgeSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    urls: list[str] = Field(default_factory=list, max_length=20)


class KnowledgeSyncResponse(BaseModel):
    sync_job_id: str
    discovered_count: int = Field(ge=0)
    indexed_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    excluded_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    errors: dict[str, str] = Field(default_factory=dict)


class WebKnowledgeSyncResponse(KnowledgeSyncResponse):
    document_count: int = Field(ge=0)
    product_count: int = Field(ge=0)
    pending_removal_count: int = Field(ge=0)
    expired_count: int = Field(ge=0)
    published: bool
    changed_document_count: int = Field(ge=0)
    duplicate_count: int = Field(ge=0)
    http_not_modified_count: int = Field(ge=0)
    duplicate_product_count: int = Field(ge=0)
    duplicate_product_total: int = Field(default=0, ge=0)
    duplicate_product_excluded_count: int = Field(default=0, ge=0)
    duplicate_product_conflict_warning_count: int = Field(default=0, ge=0)
    duplicate_product_unresolved_count: int = Field(default=0, ge=0)
    winner_product_count: int = Field(default=0, ge=0)


class WebSyncJobCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=8, max_length=200)
    manifest_id: str = Field(min_length=8, max_length=100)
    mode: Literal["shadow", "production"] = "shadow"
    sample_size: Literal[20, 100, 200, 500] | None = 20


class WebCrawlPreflightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    translation_provider: Literal["gtranslate"] = "gtranslate"


class WebCrawlDiscoveryAttemptResponse(BaseModel):
    url: str
    source: str
    outcome: str
    final_url: str | None = None


class WebCrawlManifestResponse(BaseModel):
    manifest_id: str
    site_id: str
    base_url: str
    root_sitemap_url: str
    root_sitemap_urls: list[str] = Field(default_factory=list)
    discovery_method: str = "legacy"
    warnings: list[str] = Field(default_factory=list)
    coverage_status: str = "declared_complete"
    discovery_attempts: list[WebCrawlDiscoveryAttemptResponse] = Field(default_factory=list)
    primary_language: str
    translation_provider: str
    status: str
    fingerprint: str
    version: int = Field(ge=1)
    policy_version: str
    source_config_version: int = Field(ge=0)
    source_config_current: bool
    primary_sitemap_urls: list[str]
    translated_locales: list[str]
    excluded_sitemap_count: int = Field(ge=0)
    excluded_url_count: int = Field(ge=0)
    url_count: int = Field(ge=0)
    content_kind_counts: dict[str, int] = Field(default_factory=dict)
    blocking_reasons: list[str]
    production_sync_enabled: bool
    production_blocking_reasons: list[str]
    expires_at: datetime
    is_expired: bool
    created_by: str
    created_at: datetime


class WebSyncAvailabilityResponse(BaseModel):
    site_id: str
    crawler_enabled: bool
    site_status: str
    site_verification_status: str
    base_url_configured: bool
    worker_status: Literal["healthy", "unavailable", "unknown", "not_required"]
    max_pages: int = Field(ge=1)
    manifest_safety_ceiling: int = Field(ge=1)
    full_shadow_enabled: bool
    prepare_batch_size: int = Field(ge=1)
    worker_concurrency: int = Field(ge=1)
    domain_concurrency: int = Field(ge=1)
    preflight_ready: bool
    job_processing_ready: bool
    blocking_reasons: list[str] = Field(default_factory=list)
    schema_version: int = 2
    observed_at: datetime
    valid_until: datetime | None = None
    worker_heartbeat_at: datetime | None = None
    worker_heartbeat_age_seconds: float | None = Field(default=None, ge=0)
    worker_tenant_covered: bool
    worker: "WebSyncWorkerAvailabilityResponse"
    capabilities: "WebSyncCapabilitiesResponse"


class WebSyncCapabilityDecisionResponse(BaseModel):
    allowed: bool
    reason_codes: list[str] = Field(default_factory=list)


class WebSyncCapabilitiesResponse(BaseModel):
    preflight: WebSyncCapabilityDecisionResponse
    enqueue_shadow: WebSyncCapabilityDecisionResponse
    enqueue_production: WebSyncCapabilityDecisionResponse


class WebSyncWorkerAvailabilityResponse(BaseModel):
    status: str
    freshness: str
    coverage: str
    tenant_covered: bool
    last_heartbeat_at: datetime | None = None
    heartbeat_expires_at: datetime | None = None
    heartbeat_age_seconds: float | None = Field(default=None, ge=0)
    covered_instance_count: int = Field(ge=0)
    available_instance_count: int = Field(ge=0)


class WebSyncJobReportResponse(BaseModel):
    pipeline_sync_job_id: str
    published: bool
    discovered_count: int = Field(ge=0)
    document_count: int = Field(ge=0)
    changed_document_count: int = Field(ge=0)
    unchanged_document_count: int = Field(ge=0)
    http_not_modified_count: int = Field(ge=0)
    duplicate_count: int = Field(ge=0)
    duplicate_product_count: int = Field(ge=0)
    duplicate_product_total: int = Field(default=0, ge=0)
    duplicate_product_excluded_count: int = Field(default=0, ge=0)
    duplicate_product_conflict_warning_count: int = Field(default=0, ge=0)
    duplicate_product_unresolved_count: int = Field(default=0, ge=0)
    winner_product_count: int = Field(default=0, ge=0)
    product_count: int = Field(ge=0)
    pending_removal_count: int = Field(ge=0)
    expired_count: int = Field(ge=0)
    indexed_chunk_count: int = Field(ge=0)
    excluded_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    errors: dict[str, str] = Field(default_factory=dict)
    processed_page_count: int = Field(default=0, ge=0)
    produced_document_count: int = Field(default=0, ge=0)
    failed_page_count: int = Field(default=0, ge=0)
    unresolved_product_identity_count: int = Field(default=0, ge=0)
    blocking_issue_count: int = Field(default=0, ge=0)
    publication_block_reasons: list[str] = Field(default_factory=list)


class WebSyncJobResponse(BaseModel):
    job_id: str
    site_id: str
    base_url: str
    status: str
    trigger: str
    mode: str
    publication_status: str
    manifest_id: str
    sample_size: int | None
    phase: str
    manifest_version: int | None
    manifest_fingerprint: str | None
    prepared_count: int = Field(ge=0)
    expected_count: int = Field(ge=0)
    completed_count: int = Field(ge=0)
    succeeded_count: int = Field(ge=0)
    not_modified_count: int = Field(ge=0)
    excluded_item_count: int = Field(ge=0)
    failed_item_count: int = Field(ge=0)
    canceled_item_count: int = Field(ge=0)
    requested_by: str
    requested_at: datetime
    started_at: datetime | None
    heartbeat_at: datetime | None
    completed_at: datetime | None
    cancel_requested_at: datetime | None
    blocked_at: datetime | None
    retention_expires_at: datetime | None
    attempt_count: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    max_pages: int = Field(ge=1)
    duplicate_product_policy: str
    duplicate_product_order: str
    error_code: str | None
    error_message: str | None
    report: WebSyncJobReportResponse | None
    state_version: int = Field(ge=1)
    updated_at: datetime
    last_progress_at: datetime | None
    available_at: datetime | None
    prepare_stage: str
    prepare_cursor: int = Field(ge=0)
    claim_count: int = Field(ge=0)
    failure_attempt_count: int = Field(ge=0)
    yield_count: int = Field(ge=0)
    execution_state: str
    stale: bool
    stale_at: datetime | None
    reason_code: str | None
    next_wake_at: datetime | None
    actions: dict[str, "WebSyncJobActionResponse"]


class WebSyncJobActionResponse(BaseModel):
    allowed: bool
    reason_codes: list[str] = Field(default_factory=list)


class EnqueueWebSyncJobResponse(BaseModel):
    created: bool
    job: WebSyncJobResponse


class ListWebSyncJobsResponse(BaseModel):
    items: list[WebSyncJobResponse]


class WebSyncJobItemResponse(BaseModel):
    item_id: str
    ordinal: int = Field(ge=0)
    url: str
    source_sitemap_url: str
    content_kind: str
    status: str
    attempt_count: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    duration_ms: int | None = Field(default=None, ge=0)
    canonical_url: str | None
    product_key: str | None
    normalized_product_key: str | None = None
    normalization_version: str | None = None
    error_code: str | None
    error_message: str | None
    outcome_reason: str | None
    next_attempt_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    winner_item_id: str | None = None
    winner_url: str | None = None
    identity_source: str | None = None
    policy_version: str | None = None


class ListWebSyncJobItemsResponse(BaseModel):
    items: list[WebSyncJobItemResponse]
    offset: int = Field(ge=0)
    next_offset: int | None = Field(default=None, ge=0)


class SiteKnowledgeReadinessResponse(BaseModel):
    site_id: str
    ready: bool
    catalog_ready: bool
    policy_ready: bool
    care_ready: bool
    active_product_count: int = Field(ge=0)
    active_document_count: int = Field(ge=0)
    active_snapshot_id: str | None
    last_successful_sync_at: datetime | None
    blocking_reasons: list[str]
