from datetime import UTC, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.integrations.postgres.models.base import Base


class ExperienceEventModel(Base):
    __tablename__ = "experience_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "event_id"),
        UniqueConstraint("tenant_id", "idempotency_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    event_id: Mapped[str] = mapped_column(String(100), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    site_id: Mapped[str] = mapped_column(String(100), index=True)
    conversation_id: Mapped[str] = mapped_column(String(100), index=True)
    issue_id: Mapped[str | None] = mapped_column(String(100), index=True, nullable=True)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    source: Mapped[str] = mapped_column(String(50))
    resource_type: Mapped[str] = mapped_column(String(100))
    resource_id: Mapped[str] = mapped_column(String(100))
    trace_id: Mapped[str | None] = mapped_column(String(100), index=True, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    retention_class: Mapped[str] = mapped_column(String(50), default="tenant_experience")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class IssueOutcomeEpisodeModel(Base):
    __tablename__ = "issue_outcome_episodes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "episode_id"),
        UniqueConstraint("tenant_id", "issue_id", "revision"),
        ForeignKeyConstraint(
            ("tenant_id", "conversation_id"),
            ("conversations.tenant_id", "conversations.conversation_id"),
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    episode_id: Mapped[str] = mapped_column(String(100), index=True)
    issue_id: Mapped[str] = mapped_column(String(100), index=True)
    site_id: Mapped[str] = mapped_column(String(100), index=True)
    conversation_id: Mapped[str] = mapped_column(String(100), index=True)
    actor_cohort_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    intent: Mapped[str] = mapped_column(String(100), index=True)
    status: Mapped[str] = mapped_column(String(50), index=True)
    handoff_id: Mapped[str | None] = mapped_column(String(100), index=True, nullable=True)
    trigger_trace_id: Mapped[str | None] = mapped_column(String(100), index=True, nullable=True)
    source_message_ids: Mapped[list] = mapped_column(JSON, default=list)
    issue_summary: Mapped[str] = mapped_column(Text, default="")
    ai_attempt_summary: Mapped[str] = mapped_column(Text, default="")
    human_resolution_summary: Mapped[str] = mapped_column(Text, default="")
    handoff_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    correction_labels: Mapped[list] = mapped_column(JSON, default=list)
    product_references: Mapped[list] = mapped_column(JSON, default=list)
    evidence_references: Mapped[list] = mapped_column(JSON, default=list)
    dependencies: Mapped[dict] = mapped_column(JSON, default=dict)
    outcome_evidence_grade: Mapped[str] = mapped_column(String(30), default="unknown")
    resolution_status: Mapped[str] = mapped_column(String(30), default="open", index=True)
    learning_outcome: Mapped[str] = mapped_column(String(50), default="unknown", index=True)
    outcome_source: Mapped[str] = mapped_column(String(30), default="unknown", index=True)
    outcome_conflict_status: Mapped[str] = mapped_column(String(30), default="none", index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    outcome_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ConversationSummarySegmentModel(Base):
    __tablename__ = "conversation_summary_segments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "segment_id"),
        UniqueConstraint("tenant_id", "conversation_id", "source_hash"),
        ForeignKeyConstraint(
            ("tenant_id", "conversation_id"),
            ("conversations.tenant_id", "conversations.conversation_id"),
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    segment_id: Mapped[str] = mapped_column(String(100), index=True)
    conversation_id: Mapped[str] = mapped_column(String(100), index=True)
    site_id: Mapped[str] = mapped_column(String(100), index=True)
    first_message_db_id: Mapped[int] = mapped_column(Integer)
    last_message_db_id: Mapped[int] = mapped_column(Integer)
    source_message_ids: Mapped[list] = mapped_column(JSON, default=list)
    source_hash: Mapped[str] = mapped_column(String(64), index=True)
    content: Mapped[str] = mapped_column(Text)
    structured_facts: Mapped[dict] = mapped_column(JSON, default=dict)
    fact_status: Mapped[str] = mapped_column(String(30), default="unverified")
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    superseded_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class TenantCaseMemoryModel(Base):
    __tablename__ = "tenant_case_memories"
    __table_args__ = (
        UniqueConstraint("tenant_id", "memory_id"),
        UniqueConstraint("tenant_id", "source_episode_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    memory_id: Mapped[str] = mapped_column(String(100), index=True)
    site_id: Mapped[str] = mapped_column(String(100), index=True)
    source_episode_id: Mapped[str] = mapped_column(String(100), index=True)
    source_actor_cohort_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    issue_signature: Mapped[str] = mapped_column(String(64), index=True)
    polarity: Mapped[str] = mapped_column(String(20), index=True)
    summary: Mapped[str] = mapped_column(Text)
    guidance: Mapped[dict] = mapped_column(JSON, default=dict)
    applicability: Mapped[dict] = mapped_column(JSON, default=dict)
    dependencies: Mapped[dict] = mapped_column(JSON, default=dict)
    outcome_evidence_grade: Mapped[str] = mapped_column(String(30), default="unknown")
    freshness: Mapped[str] = mapped_column(String(30), default="current", index=True)
    status: Mapped[str] = mapped_column(String(30), default="shadow", index=True)
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    negative_transfer_count: Mapped[int] = mapped_column(Integer, default=0)
    uncertainty: Mapped[float] = mapped_column(Float, default=1.0)
    wilson_lower_bound: Mapped[float] = mapped_column(Float, default=0.0)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(384), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_by: Mapped[str | None] = mapped_column(String(100), nullable=True)


class TenantPatternMemoryModel(Base):
    __tablename__ = "tenant_pattern_memories"
    __table_args__ = (
        UniqueConstraint("tenant_id", "pattern_id"),
        UniqueConstraint("tenant_id", "fingerprint"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    pattern_id: Mapped[str] = mapped_column(String(100), index=True)
    site_id: Mapped[str | None] = mapped_column(String(100), index=True, nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    guidance: Mapped[dict] = mapped_column(JSON, default=dict)
    source_memory_ids: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(30), default="shadow", index=True)
    needs_rebuild: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ExperienceMemoryUsageModel(Base):
    __tablename__ = "experience_memory_usages"
    __table_args__ = (
        UniqueConstraint("tenant_id", "usage_id"),
        UniqueConstraint("tenant_id", "trace_id", "memory_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    usage_id: Mapped[str] = mapped_column(String(100), index=True)
    conversation_id: Mapped[str] = mapped_column(String(100), index=True)
    issue_id: Mapped[str | None] = mapped_column(String(100), index=True, nullable=True)
    trace_id: Mapped[str] = mapped_column(String(100), index=True)
    memory_id: Mapped[str] = mapped_column(String(100), index=True)
    release_version: Mapped[str] = mapped_column(String(100), index=True)
    treatment: Mapped[str] = mapped_column(String(30), index=True)
    retrieved: Mapped[bool] = mapped_column(Boolean, default=True)
    eligible: Mapped[bool] = mapped_column(Boolean, default=True)
    selected: Mapped[bool] = mapped_column(Boolean, default=True)
    influenced: Mapped[bool] = mapped_column(Boolean, default=False)
    outcome_attributed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    used_for: Mapped[list] = mapped_column(JSON, default=list)
    outcome: Mapped[str | None] = mapped_column(String(50), index=True, nullable=True)
    outcome_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ExperienceReleaseModel(Base):
    __tablename__ = "experience_releases"
    __table_args__ = (UniqueConstraint("tenant_id", "release_version"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    release_version: Mapped[str] = mapped_column(String(100), index=True)
    mode: Mapped[str] = mapped_column(String(30), index=True)
    rollout_percent: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(30), index=True)
    gate_report: Mapped[dict] = mapped_column(JSON, default=dict)
    manifest: Mapped[dict] = mapped_column(JSON, default=dict)
    dataset_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    guardrail_report: Mapped[dict] = mapped_column(JSON, default=dict)
    approved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExperienceExperimentModel(Base):
    __tablename__ = "experience_experiments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "assignment_id"),
        UniqueConstraint("tenant_id", "conversation_id", "release_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    assignment_id: Mapped[str] = mapped_column(String(100), index=True)
    conversation_id: Mapped[str] = mapped_column(String(100), index=True)
    issue_id: Mapped[str | None] = mapped_column(String(100), index=True, nullable=True)
    release_version: Mapped[str] = mapped_column(String(100), index=True)
    treatment: Mapped[str] = mapped_column(String(30), index=True)
    assignment_key_hash: Mapped[str] = mapped_column(String(64))
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ExperienceEvalRunModel(Base):
    __tablename__ = "experience_eval_runs"
    __table_args__ = (UniqueConstraint("tenant_id", "eval_run_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    eval_run_id: Mapped[str] = mapped_column(String(100), index=True)
    release_version: Mapped[str] = mapped_column(String(100), index=True)
    evaluation_mode: Mapped[str] = mapped_column(String(30), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    dataset_version: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ImprovementCandidateModel(Base):
    __tablename__ = "improvement_candidates"
    __table_args__ = (UniqueConstraint("tenant_id", "candidate_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    candidate_id: Mapped[str] = mapped_column(String(100), index=True)
    candidate_type: Mapped[str] = mapped_column(String(50), index=True)
    root_cause: Mapped[str] = mapped_column(String(100), index=True)
    source_pattern_ids: Mapped[list] = mapped_column(JSON, default=list)
    proposed_change: Mapped[dict] = mapped_column(JSON, default=dict)
    authoritative_source_refs: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(30), default="draft", index=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    review_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    eval_run_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    asset_version_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
