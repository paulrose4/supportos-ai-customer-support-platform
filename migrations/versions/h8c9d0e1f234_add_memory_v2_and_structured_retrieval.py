"""add memory v2 and structured retrieval metadata

Revision ID: h8c9d0e1f234
Revises: g7b8c9d0e123
Create Date: 2026-07-22 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "h8c9d0e1f234"
down_revision: str | None = "g7b8c9d0e123"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "customer_memory_items",
        sa.Column("status", sa.String(length=30), server_default="active", nullable=False),
    )
    op.add_column(
        "customer_memory_items",
        sa.Column(
            "normalized_value",
            sa.JSON(),
            server_default=sa.text("'{}'::json"),
            nullable=False,
        ),
    )
    op.add_column(
        "customer_memory_items",
        sa.Column("sensitivity", sa.String(length=30), server_default="standard", nullable=False),
    )
    op.add_column(
        "customer_memory_items",
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "customer_memory_items",
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "customer_memory_items",
        sa.Column("use_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "customer_memory_items", sa.Column("superseded_by", sa.String(length=100), nullable=True)
    )
    op.add_column(
        "customer_memory_items",
        sa.Column("legacy", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.create_index(
        "ix_customer_memory_items_status", "customer_memory_items", ["status"], unique=False
    )
    op.create_index(
        "ix_customer_memory_items_sensitivity",
        "customer_memory_items",
        ["sensitivity"],
        unique=False,
    )
    op.execute(
        """
        UPDATE customer_memory_items
        SET expires_at = updated_at + CASE kind
            WHEN 'verified_product' THEN INTERVAL '180 days'
            WHEN 'troubleshooting' THEN INTERVAL '30 days'
            ELSE INTERVAL '90 days'
        END
        WHERE expires_at IS NULL
        """
    )

    op.create_table(
        "memory_candidates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("candidate_id", sa.String(length=100), nullable=False),
        sa.Column("candidate_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("customer_id", sa.String(length=100), nullable=False),
        sa.Column("conversation_id", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=50), nullable=False),
        sa.Column("normalized_value", sa.JSON(), nullable=False),
        sa.Column("display_text", sa.Text(), nullable=False),
        sa.Column("source_message_ids", sa.JSON(), nullable=False),
        sa.Column("source_trace_id", sa.String(length=100), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("sensitivity", sa.String(length=30), nullable=False),
        sa.Column("consent_required", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("rejection_reason", sa.String(length=500), nullable=True),
        sa.Column("reviewed_by", sa.String(length=100), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "customer_id"],
            ["customers.tenant_id", "customers.customer_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["conversations.tenant_id", "conversations.conversation_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "candidate_id"),
        sa.UniqueConstraint("tenant_id", "customer_id", "candidate_fingerprint"),
    )
    for column in (
        "tenant_id",
        "candidate_id",
        "candidate_fingerprint",
        "customer_id",
        "conversation_id",
        "kind",
        "source_trace_id",
        "sensitivity",
        "status",
        "expires_at",
    ):
        op.create_index(f"ix_memory_candidates_{column}", "memory_candidates", [column])

    op.create_table(
        "resolution_episodes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("episode_id", sa.String(length=100), nullable=False),
        sa.Column("customer_id", sa.String(length=100), nullable=False),
        sa.Column("conversation_id", sa.String(length=100), nullable=False),
        sa.Column("intent", sa.String(length=100), nullable=False),
        sa.Column("issue_summary", sa.Text(), nullable=False),
        sa.Column("product_reference", sa.String(length=100), nullable=True),
        sa.Column("order_reference", sa.String(length=100), nullable=True),
        sa.Column("actions_taken", sa.JSON(), nullable=False),
        sa.Column("resolution_summary", sa.Text(), nullable=False),
        sa.Column("resolution_source", sa.String(length=50), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reopened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "customer_id"],
            ["customers.tenant_id", "customers.customer_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["conversations.tenant_id", "conversations.conversation_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "episode_id"),
        sa.UniqueConstraint("tenant_id", "conversation_id", "resolution_source"),
    )
    for column in (
        "tenant_id",
        "episode_id",
        "customer_id",
        "conversation_id",
        "intent",
        "resolution_source",
        "resolved_at",
        "expires_at",
        "status",
    ):
        op.create_index(f"ix_resolution_episodes_{column}", "resolution_episodes", [column])

    op.add_column(
        "knowledge_chunk_manifests",
        sa.Column("chunk_type", sa.String(length=50), server_default="general", nullable=False),
    )
    op.add_column(
        "knowledge_chunk_manifests",
        sa.Column("parent_chunk_id", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "knowledge_chunk_manifests",
        sa.Column("section_path", sa.JSON(), server_default=sa.text("'[]'::json"), nullable=False),
    )
    op.add_column(
        "knowledge_chunk_manifests",
        sa.Column("fact_keys", sa.JSON(), server_default=sa.text("'[]'::json"), nullable=False),
    )
    op.create_index(
        "ix_knowledge_chunk_manifests_chunk_type",
        "knowledge_chunk_manifests",
        ["chunk_type"],
    )
    op.create_index(
        "ix_knowledge_chunk_manifests_parent_chunk_id",
        "knowledge_chunk_manifests",
        ["parent_chunk_id"],
    )

    op.add_column(
        "knowledge_links",
        sa.Column(
            "relation_type", sa.String(length=80), server_default="references", nullable=False
        ),
    )
    op.add_column(
        "knowledge_links",
        sa.Column(
            "relation_metadata",
            sa.JSON(),
            server_default=sa.text("'{}'::json"),
            nullable=False,
        ),
    )
    op.add_column(
        "knowledge_links", sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "knowledge_links", sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index("ix_knowledge_links_relation_type", "knowledge_links", ["relation_type"])


def downgrade() -> None:
    op.drop_index("ix_knowledge_links_relation_type", table_name="knowledge_links")
    for column in ("effective_to", "effective_from", "relation_metadata", "relation_type"):
        op.drop_column("knowledge_links", column)

    op.drop_index(
        "ix_knowledge_chunk_manifests_parent_chunk_id", table_name="knowledge_chunk_manifests"
    )
    op.drop_index("ix_knowledge_chunk_manifests_chunk_type", table_name="knowledge_chunk_manifests")
    for column in ("fact_keys", "section_path", "parent_chunk_id", "chunk_type"):
        op.drop_column("knowledge_chunk_manifests", column)

    op.drop_table("resolution_episodes")
    op.drop_table("memory_candidates")
    op.drop_index("ix_customer_memory_items_sensitivity", table_name="customer_memory_items")
    op.drop_index("ix_customer_memory_items_status", table_name="customer_memory_items")
    for column in (
        "legacy",
        "superseded_by",
        "use_count",
        "last_used_at",
        "last_verified_at",
        "sensitivity",
        "normalized_value",
        "status",
    ):
        op.drop_column("customer_memory_items", column)
