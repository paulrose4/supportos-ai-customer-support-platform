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


class KnowledgeDocumentModel(Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "document_id"),
        CheckConstraint(
            "char_length(title) <= 500",
            name="title_length",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    document_id: Mapped[str] = mapped_column(String(100), index=True)
    source_path: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(30), index=True)
    current_version_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class KnowledgeDocumentVersionModel(Base):
    __tablename__ = "knowledge_document_versions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "version_id"),
        Index(
            "ix_knowledge_document_versions_document_content_hash",
            "tenant_id",
            "document_id",
            "content_hash",
        ),
        Index(
            "ix_knowledge_document_versions_snapshot_id",
            "tenant_id",
            "snapshot_id",
        ),
        Index(
            "ix_knowledge_document_versions_lifecycle_state",
            "tenant_id",
            "lifecycle_state",
        ),
        Index(
            "uq_kdv_tenant_snapshot_document_live",
            "tenant_id",
            "snapshot_id",
            "document_id",
            unique=True,
            postgresql_where=text("snapshot_id IS NOT NULL AND lifecycle_state <> 'discarded'"),
        ),
        CheckConstraint(
            "lifecycle_state IN "
            "('draft', 'staged', 'indexed', 'publishable', 'active', 'discarded')",
            name="knowledge_version_lifecycle_state",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "document_id"),
            ("knowledge_documents.tenant_id", "knowledge_documents.document_id"),
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    version_id: Mapped[str] = mapped_column(String(100), index=True)
    document_id: Mapped[str] = mapped_column(String(100), index=True)
    version: Mapped[str] = mapped_column(String(100))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    snapshot_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lifecycle_state: Mapped[str] = mapped_column(
        String(30), default="draft", server_default="draft"
    )
    index_status: Mapped[str] = mapped_column(
        String(30), default="pending", server_default="pending", index=True
    )
    authority_level: Mapped[int] = mapped_column(Integer)
    priority: Mapped[int] = mapped_column(Integer)
    language: Mapped[str] = mapped_column(String(30), index=True)
    audience: Mapped[str] = mapped_column(String(50), index=True)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class KnowledgeChunkManifestModel(Base):
    __tablename__ = "knowledge_chunk_manifests"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "version_id",
            "chunk_id",
            name="uq_knowledge_chunk_manifests_tenant_version_chunk",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "version_id"),
            ("knowledge_document_versions.tenant_id", "knowledge_document_versions.version_id"),
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "heading IS NULL OR char_length(heading) <= 500",
            name="heading_length",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    chunk_id: Mapped[str] = mapped_column(String(100), index=True)
    version_id: Mapped[str] = mapped_column(String(100), index=True)
    document_id: Mapped[str] = mapped_column(String(100), index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    sequence: Mapped[int] = mapped_column(Integer)
    heading: Mapped[str | None] = mapped_column(String(500), nullable=True)
    chunk_type: Mapped[str] = mapped_column(String(50), default="general", index=True)
    parent_chunk_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    section_path: Mapped[list] = mapped_column(JSON, default=list)
    fact_keys: Mapped[list] = mapped_column(JSON, default=list)
    qdrant_point_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class KnowledgeLinkModel(Base):
    __tablename__ = "knowledge_links"
    __table_args__ = (
        UniqueConstraint("tenant_id", "link_id"),
        ForeignKeyConstraint(
            ("tenant_id", "version_id"),
            ("knowledge_document_versions.tenant_id", "knowledge_document_versions.version_id"),
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    link_id: Mapped[str] = mapped_column(String(100), index=True)
    version_id: Mapped[str] = mapped_column(String(100), index=True)
    source_document_id: Mapped[str] = mapped_column(String(100), index=True)
    target_reference: Mapped[str] = mapped_column(String(500))
    resolved_document_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    relation_type: Mapped[str] = mapped_column(String(80), default="references", index=True)
    relation_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class KnowledgeSyncJobModel(Base):
    __tablename__ = "knowledge_sync_jobs"
    __table_args__ = (UniqueConstraint("tenant_id", "sync_job_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    sync_job_id: Mapped[str] = mapped_column(String(100), index=True)
    vault_path: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), index=True)
    discovered_count: Mapped[int] = mapped_column(Integer, default=0)
    indexed_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    error_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KnowledgeConflictModel(Base):
    __tablename__ = "knowledge_conflicts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "conflict_id"),
        UniqueConstraint("tenant_id", "conflict_fingerprint"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    conflict_id: Mapped[str] = mapped_column(String(100), index=True)
    conflict_fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), default="open", index=True)
    risk_level: Mapped[int] = mapped_column(Integer)
    question_key: Mapped[str] = mapped_column(String(500))
    affected_version_ids: Mapped[list] = mapped_column(JSON)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
