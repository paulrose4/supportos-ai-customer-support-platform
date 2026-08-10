from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.integrations.postgres.models.base import Base


class WebSyncJobModel(Base):
    __tablename__ = "web_sync_jobs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "job_id"),
        UniqueConstraint("tenant_id", "idempotency_key"),
        UniqueConstraint("active_site_key"),
        ForeignKeyConstraint(
            ("tenant_id", "site_id"),
            ("support_sites.tenant_id", "support_sites.site_id"),
            ondelete="RESTRICT",
        ),
        Index(
            "ix_web_sync_jobs_active_claim",
            "tenant_id",
            "requested_at",
            "id",
            postgresql_where=text(
                "status IN ('preparing', 'queued', 'running', 'blocked', 'cleanup_pending')"
            ),
        ),
        Index(
            "ix_web_sync_jobs_lease_recovery",
            "tenant_id",
            "lease_expires_at",
            "requested_at",
            "id",
            postgresql_where=text(
                "status IN ('preparing', 'running') AND lease_expires_at IS NOT NULL"
            ),
        ),
        Index(
            "ix_web_sync_jobs_tenant_available",
            "tenant_id",
            "available_at",
            "requested_at",
            "id",
            postgresql_where=text("status IN ('preparing', 'queued', 'cleanup_pending')"),
        ),
        CheckConstraint(
            "claim_count >= 0 AND failure_attempt_count >= 0 AND yield_count >= 0 "
            "AND state_version >= 1 AND prepare_cursor >= 0",
            name="web_sync_jobs_scheduling_counts_nonnegative",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "site_id", "manifest_id"),
            (
                "web_crawl_manifests.tenant_id",
                "web_crawl_manifests.site_id",
                "web_crawl_manifests.manifest_id",
            ),
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    job_id: Mapped[str] = mapped_column(String(100), index=True)
    site_id: Mapped[str] = mapped_column(String(100), index=True)
    base_url: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), index=True)
    trigger: Mapped[str] = mapped_column(String(30), index=True)
    mode: Mapped[str] = mapped_column(String(30), default="production", index=True)
    publication_status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    manifest_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    sample_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    phase: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    expected_count: Mapped[int] = mapped_column(Integer, default=0)
    prepared_count: Mapped[int] = mapped_column(Integer, default=0)
    manifest_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    manifest_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    completed_count: Mapped[int] = mapped_column(Integer, default=0)
    succeeded_count: Mapped[int] = mapped_column(Integer, default=0)
    not_modified_count: Mapped[int] = mapped_column(Integer, default=0)
    excluded_item_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_item_count: Mapped[int] = mapped_column(Integer, default=0)
    canceled_item_count: Mapped[int] = mapped_column(Integer, default=0)
    request_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    requested_by: Mapped[str] = mapped_column(String(100), index=True)
    correlation_id: Mapped[str] = mapped_column(String(100), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    active_site_key: Mapped[str | None] = mapped_column(String(250), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    claim_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    yield_count: Mapped[int] = mapped_column(Integer, default=0)
    state_version: Mapped[int] = mapped_column(Integer, default=1)
    prepare_stage: Mapped[str] = mapped_column(String(50), default="copy_manifest_items")
    prepare_cursor: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    report_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )
    last_progress_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    blocked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    retention_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )


class WebSyncJobItemModel(Base):
    __tablename__ = "web_sync_job_items"
    __table_args__ = (
        UniqueConstraint("tenant_id", "job_id", "item_id"),
        UniqueConstraint("tenant_id", "job_id", "url"),
        ForeignKeyConstraint(
            ("tenant_id", "job_id"),
            ("web_sync_jobs.tenant_id", "web_sync_jobs.job_id"),
            ondelete="CASCADE",
        ),
        Index(
            "ix_web_sync_job_items_pending_due",
            "tenant_id",
            "job_id",
            "next_attempt_at",
            "ordinal",
            "id",
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "ix_web_sync_job_items_fetching_recovery",
            "tenant_id",
            "job_id",
            "lease_expires_at",
            "ordinal",
            "id",
            postgresql_where=text("status = 'fetching'"),
        ),
        Index(
            "ix_web_sync_job_items_duplicate_policy",
            "tenant_id",
            "job_id",
            "product_key",
            "ordinal",
        ),
        Index(
            "ix_web_sync_job_items_normalized_product_key",
            "tenant_id",
            "job_id",
            "policy_version",
            "normalized_product_key",
            "ordinal",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    job_id: Mapped[str] = mapped_column(String(100), index=True)
    site_id: Mapped[str] = mapped_column(String(100), index=True)
    manifest_id: Mapped[str] = mapped_column(String(100), index=True)
    item_id: Mapped[str] = mapped_column(String(100), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    url: Mapped[str] = mapped_column(Text)
    source_sitemap_url: Mapped[str] = mapped_column(Text)
    content_kind: Mapped[str] = mapped_column(String(30), default="general", index=True)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    lease_owner: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    document_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    version_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    canonical_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    etag: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_modified: Mapped[str | None] = mapped_column(String(200), nullable=True)
    product_key: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    normalized_product_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    normalization_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    winner_item_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    winner_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    identity_source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    policy_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    report_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome_reason: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)


class WebSyncProductIdentityDecisionModel(Base):
    __tablename__ = "web_sync_product_identity_decisions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "job_id",
            "normalized_product_key",
            name="uq_web_sync_product_identity_decision",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "job_id"),
            ("web_sync_jobs.tenant_id", "web_sync_jobs.job_id"),
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "job_id", "winner_item_id"),
            (
                "web_sync_job_items.tenant_id",
                "web_sync_job_items.job_id",
                "web_sync_job_items.item_id",
            ),
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "decision_revision >= 1 AND normalized_product_key <> '' "
            "AND state IN ('selected', 'promoted', 'unresolved')",
            name="ck_web_sync_product_identity_decision_valid",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    job_id: Mapped[str] = mapped_column(String(100), index=True)
    normalized_product_key: Mapped[str] = mapped_column(String(500), index=True)
    winner_item_id: Mapped[str] = mapped_column(String(100), index=True)
    winner_url: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(30), default="selected", index=True)
    policy_version: Mapped[str] = mapped_column(String(100))
    normalization_version: Mapped[str] = mapped_column(String(100))
    decision_revision: Mapped[int] = mapped_column(Integer, default=1)
    decision_reason: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
