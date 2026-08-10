"""add support operations control plane

Revision ID: c71d3d7f4b10
Revises: a5bdb12a805c
Create Date: 2026-07-15 16:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c71d3d7f4b10"
down_revision: Union[str, None] = "a5bdb12a805c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "support_sites",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("site_id", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_support_sites")),
        sa.UniqueConstraint("tenant_id", "site_id", name=op.f("uq_support_sites_tenant_id_site_id")),
    )
    op.create_index(op.f("ix_support_sites_tenant_id"), "support_sites", ["tenant_id"])
    op.create_index(op.f("ix_support_sites_site_id"), "support_sites", ["site_id"])
    op.create_index(op.f("ix_support_sites_status"), "support_sites", ["status"])

    op.create_table(
        "customer_memory_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("memory_id", sa.String(length=100), nullable=False),
        sa.Column("customer_id", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=50), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("source_id", sa.String(length=100), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("consent_status", sa.String(length=30), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "customer_id"],
            ["customers.tenant_id", "customers.customer_id"],
            name=op.f("fk_customer_memory_items_tenant_id_customer_id_customers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customer_memory_items")),
        sa.UniqueConstraint(
            "tenant_id", "memory_id", name=op.f("uq_customer_memory_items_tenant_id_memory_id")
        ),
    )
    op.create_index(op.f("ix_customer_memory_items_tenant_id"), "customer_memory_items", ["tenant_id"])
    op.create_index(op.f("ix_customer_memory_items_memory_id"), "customer_memory_items", ["memory_id"])
    op.create_index(op.f("ix_customer_memory_items_customer_id"), "customer_memory_items", ["customer_id"])
    op.create_index(op.f("ix_customer_memory_items_kind"), "customer_memory_items", ["kind"])

    op.create_table(
        "support_operation_requests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("operation", sa.String(length=100), nullable=False),
        sa.Column("resource_type", sa.String(length=100), nullable=False),
        sa.Column("resource_id", sa.String(length=100), nullable=False),
        sa.Column("response_snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_support_operation_requests")),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name=op.f("uq_support_operation_requests_tenant_id_idempotency_key"),
        ),
    )
    op.create_index(op.f("ix_support_operation_requests_tenant_id"), "support_operation_requests", ["tenant_id"])
    op.create_index(op.f("ix_support_operation_requests_idempotency_key"), "support_operation_requests", ["idempotency_key"])

    op.add_column("conversations", sa.Column("site_id", sa.String(length=100), nullable=True))
    op.add_column("conversations", sa.Column("ownership_mode", sa.String(length=30), server_default="ai", nullable=False))
    op.add_column("conversations", sa.Column("assigned_agent_id", sa.String(length=100), nullable=True))
    op.add_column("conversations", sa.Column("risk_level", sa.Integer(), server_default="0", nullable=False))
    op.add_column("conversations", sa.Column("unread_count", sa.Integer(), server_default="0", nullable=False))
    op.add_column("conversations", sa.Column("identity_verified", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("conversations", sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f("ix_conversations_site_id"), "conversations", ["site_id"])
    op.create_index(op.f("ix_conversations_ownership_mode"), "conversations", ["ownership_mode"])
    op.create_index(op.f("ix_conversations_assigned_agent_id"), "conversations", ["assigned_agent_id"])
    op.create_index(op.f("ix_conversations_last_message_at"), "conversations", ["last_message_at"])

    op.add_column("messages", sa.Column("message_type", sa.String(length=30), server_default="chat", nullable=False))
    op.add_column("messages", sa.Column("author_subject_id", sa.String(length=100), nullable=True))
    op.add_column("messages", sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"))

    op.add_column("handoff_requests", sa.Column("priority", sa.String(length=30), server_default="normal", nullable=False))
    op.add_column("handoff_requests", sa.Column("assigned_agent_id", sa.String(length=100), nullable=True))
    op.add_column("handoff_requests", sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("handoff_requests", sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f("ix_handoff_requests_priority"), "handoff_requests", ["priority"])
    op.create_index(op.f("ix_handoff_requests_assigned_agent_id"), "handoff_requests", ["assigned_agent_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_handoff_requests_assigned_agent_id"), table_name="handoff_requests")
    op.drop_index(op.f("ix_handoff_requests_priority"), table_name="handoff_requests")
    op.drop_column("handoff_requests", "resolved_at")
    op.drop_column("handoff_requests", "accepted_at")
    op.drop_column("handoff_requests", "assigned_agent_id")
    op.drop_column("handoff_requests", "priority")

    op.drop_column("messages", "metadata")
    op.drop_column("messages", "author_subject_id")
    op.drop_column("messages", "message_type")

    op.drop_index(op.f("ix_conversations_last_message_at"), table_name="conversations")
    op.drop_index(op.f("ix_conversations_assigned_agent_id"), table_name="conversations")
    op.drop_index(op.f("ix_conversations_ownership_mode"), table_name="conversations")
    op.drop_index(op.f("ix_conversations_site_id"), table_name="conversations")
    op.drop_column("conversations", "last_message_at")
    op.drop_column("conversations", "identity_verified")
    op.drop_column("conversations", "unread_count")
    op.drop_column("conversations", "risk_level")
    op.drop_column("conversations", "assigned_agent_id")
    op.drop_column("conversations", "ownership_mode")
    op.drop_column("conversations", "site_id")

    op.drop_index(op.f("ix_support_operation_requests_idempotency_key"), table_name="support_operation_requests")
    op.drop_index(op.f("ix_support_operation_requests_tenant_id"), table_name="support_operation_requests")
    op.drop_table("support_operation_requests")
    op.drop_index(op.f("ix_customer_memory_items_kind"), table_name="customer_memory_items")
    op.drop_index(op.f("ix_customer_memory_items_customer_id"), table_name="customer_memory_items")
    op.drop_index(op.f("ix_customer_memory_items_memory_id"), table_name="customer_memory_items")
    op.drop_index(op.f("ix_customer_memory_items_tenant_id"), table_name="customer_memory_items")
    op.drop_table("customer_memory_items")
    op.drop_index(op.f("ix_support_sites_status"), table_name="support_sites")
    op.drop_index(op.f("ix_support_sites_site_id"), table_name="support_sites")
    op.drop_index(op.f("ix_support_sites_tenant_id"), table_name="support_sites")
    op.drop_table("support_sites")
