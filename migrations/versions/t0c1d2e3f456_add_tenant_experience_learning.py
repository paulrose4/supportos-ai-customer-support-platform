"""add tenant experience learning control plane

Revision ID: t0c1d2e3f456
Revises: s9b0c1d2e345
Create Date: 2026-07-31 22:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "t0c1d2e3f456"
down_revision: str | None = "s9b0c1d2e345"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (
    "experience_events",
    "issue_outcome_episodes",
    "conversation_summary_segments",
    "tenant_case_memories",
    "tenant_pattern_memories",
    "experience_memory_usages",
    "experience_releases",
    "experience_experiments",
    "experience_eval_runs",
    "improvement_candidates",
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "experience_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(100), nullable=False),
        sa.Column("event_id", sa.String(100), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("site_id", sa.String(100), nullable=False),
        sa.Column("conversation_id", sa.String(100), nullable=False),
        sa.Column("issue_id", sa.String(100), nullable=True),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("resource_type", sa.String(100), nullable=False),
        sa.Column("resource_id", sa.String(100), nullable=False),
        sa.Column("trace_id", sa.String(100), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("retention_class", sa.String(50), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "event_id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key"),
    )
    op.create_table(
        "issue_outcome_episodes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(100), nullable=False),
        sa.Column("episode_id", sa.String(100), nullable=False),
        sa.Column("issue_id", sa.String(100), nullable=False),
        sa.Column("site_id", sa.String(100), nullable=False),
        sa.Column("conversation_id", sa.String(100), nullable=False),
        sa.Column("intent", sa.String(100), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("handoff_id", sa.String(100), nullable=True),
        sa.Column("trigger_trace_id", sa.String(100), nullable=True),
        sa.Column("source_message_ids", sa.JSON(), nullable=False),
        sa.Column("issue_summary", sa.Text(), nullable=False),
        sa.Column("ai_attempt_summary", sa.Text(), nullable=False),
        sa.Column("human_resolution_summary", sa.Text(), nullable=False),
        sa.Column("handoff_reason", sa.String(100), nullable=True),
        sa.Column("correction_labels", sa.JSON(), nullable=False),
        sa.Column("product_references", sa.JSON(), nullable=False),
        sa.Column("evidence_references", sa.JSON(), nullable=False),
        sa.Column("dependencies", sa.JSON(), nullable=False),
        sa.Column("outcome_evidence_grade", sa.String(30), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["conversations.tenant_id", "conversations.conversation_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "episode_id"),
        sa.UniqueConstraint("tenant_id", "issue_id", "revision"),
    )
    op.create_table(
        "conversation_summary_segments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(100), nullable=False),
        sa.Column("segment_id", sa.String(100), nullable=False),
        sa.Column("conversation_id", sa.String(100), nullable=False),
        sa.Column("site_id", sa.String(100), nullable=False),
        sa.Column("first_message_db_id", sa.Integer(), nullable=False),
        sa.Column("last_message_db_id", sa.Integer(), nullable=False),
        sa.Column("source_message_ids", sa.JSON(), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("structured_facts", sa.JSON(), nullable=False),
        sa.Column("fact_status", sa.String(30), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("superseded_by", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["conversations.tenant_id", "conversations.conversation_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "segment_id"),
        sa.UniqueConstraint("tenant_id", "conversation_id", "source_hash"),
    )
    op.create_table(
        "tenant_case_memories",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(100), nullable=False),
        sa.Column("memory_id", sa.String(100), nullable=False),
        sa.Column("site_id", sa.String(100), nullable=False),
        sa.Column("source_episode_id", sa.String(100), nullable=False),
        sa.Column("issue_signature", sa.String(64), nullable=False),
        sa.Column("polarity", sa.String(20), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("guidance", sa.JSON(), nullable=False),
        sa.Column("applicability", sa.JSON(), nullable=False),
        sa.Column("dependencies", sa.JSON(), nullable=False),
        sa.Column("outcome_evidence_grade", sa.String(30), nullable=False),
        sa.Column("freshness", sa.String(30), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("success_count", sa.Integer(), nullable=False),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("negative_transfer_count", sa.Integer(), nullable=False),
        sa.Column("uncertainty", sa.Float(), nullable=False),
        sa.Column("wilson_lower_bound", sa.Float(), nullable=False),
        sa.Column("embedding", Vector(384), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by", sa.String(100), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "memory_id"),
        sa.UniqueConstraint("tenant_id", "source_episode_id"),
    )
    op.create_table(
        "tenant_pattern_memories",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(100), nullable=False),
        sa.Column("pattern_id", sa.String(100), nullable=False),
        sa.Column("site_id", sa.String(100), nullable=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("guidance", sa.JSON(), nullable=False),
        sa.Column("source_memory_ids", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("needs_rebuild", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "pattern_id"),
        sa.UniqueConstraint("tenant_id", "fingerprint"),
    )
    op.create_table(
        "experience_memory_usages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(100), nullable=False),
        sa.Column("usage_id", sa.String(100), nullable=False),
        sa.Column("conversation_id", sa.String(100), nullable=False),
        sa.Column("issue_id", sa.String(100), nullable=True),
        sa.Column("trace_id", sa.String(100), nullable=False),
        sa.Column("memory_id", sa.String(100), nullable=False),
        sa.Column("release_version", sa.String(100), nullable=False),
        sa.Column("treatment", sa.String(30), nullable=False),
        sa.Column("retrieved", sa.Boolean(), nullable=False),
        sa.Column("used_for", sa.JSON(), nullable=False),
        sa.Column("outcome", sa.String(50), nullable=True),
        sa.Column("outcome_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "usage_id"),
        sa.UniqueConstraint("tenant_id", "trace_id", "memory_id"),
    )
    op.create_table(
        "experience_releases",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(100), nullable=False),
        sa.Column("release_version", sa.String(100), nullable=False),
        sa.Column("mode", sa.String(30), nullable=False),
        sa.Column("rollout_percent", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("gate_report", sa.JSON(), nullable=False),
        sa.Column("approved_by", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "release_version"),
    )
    op.create_table(
        "improvement_candidates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(100), nullable=False),
        sa.Column("candidate_id", sa.String(100), nullable=False),
        sa.Column("candidate_type", sa.String(50), nullable=False),
        sa.Column("root_cause", sa.String(100), nullable=False),
        sa.Column("source_pattern_ids", sa.JSON(), nullable=False),
        sa.Column("proposed_change", sa.JSON(), nullable=False),
        sa.Column("authoritative_source_refs", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("reviewed_by", sa.String(100), nullable=True),
        sa.Column("review_id", sa.String(100), nullable=True),
        sa.Column("eval_run_id", sa.String(100), nullable=True),
        sa.Column("asset_version_id", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "candidate_id"),
    )
    op.create_table(
        "experience_experiments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(100), nullable=False),
        sa.Column("assignment_id", sa.String(100), nullable=False),
        sa.Column("conversation_id", sa.String(100), nullable=False),
        sa.Column("release_version", sa.String(100), nullable=False),
        sa.Column("treatment", sa.String(30), nullable=False),
        sa.Column("assignment_key_hash", sa.String(64), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "assignment_id"),
        sa.UniqueConstraint("tenant_id", "conversation_id", "release_version"),
    )
    op.create_table(
        "experience_eval_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(100), nullable=False),
        sa.Column("eval_run_id", sa.String(100), nullable=False),
        sa.Column("release_version", sa.String(100), nullable=False),
        sa.Column("evaluation_mode", sa.String(30), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("dataset_version", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "eval_run_id"),
    )
    _create_indexes_and_rls()
    op.execute(
        "CREATE INDEX ix_tenant_case_memories_embedding_hnsw "
        "ON tenant_case_memories USING hnsw (embedding vector_cosine_ops)"
    )


def _create_indexes_and_rls() -> None:
    index_columns = {
        "experience_events": (
            "tenant_id",
            "site_id",
            "conversation_id",
            "issue_id",
            "event_type",
            "occurred_at",
        ),
        "issue_outcome_episodes": (
            "tenant_id",
            "episode_id",
            "issue_id",
            "site_id",
            "conversation_id",
            "status",
            "updated_at",
        ),
        "conversation_summary_segments": (
            "tenant_id",
            "segment_id",
            "conversation_id",
            "site_id",
            "source_hash",
            "status",
        ),
        "tenant_case_memories": (
            "tenant_id",
            "memory_id",
            "site_id",
            "source_episode_id",
            "issue_signature",
            "polarity",
            "freshness",
            "status",
        ),
        "tenant_pattern_memories": (
            "tenant_id",
            "pattern_id",
            "site_id",
            "fingerprint",
            "status",
            "needs_rebuild",
        ),
        "experience_memory_usages": (
            "tenant_id",
            "usage_id",
            "conversation_id",
            "issue_id",
            "trace_id",
            "memory_id",
            "release_version",
            "treatment",
            "outcome",
        ),
        "experience_releases": ("tenant_id", "release_version", "mode", "status"),
        "experience_experiments": (
            "tenant_id",
            "assignment_id",
            "conversation_id",
            "release_version",
            "treatment",
            "assigned_at",
        ),
        "experience_eval_runs": (
            "tenant_id",
            "eval_run_id",
            "release_version",
            "evaluation_mode",
            "status",
            "created_at",
        ),
        "improvement_candidates": (
            "tenant_id",
            "candidate_id",
            "candidate_type",
            "root_cause",
            "status",
        ),
    }
    for table, columns in index_columns.items():
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')) "
            "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))"
        )


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.drop_table(table)
