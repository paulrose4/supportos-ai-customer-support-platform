"""add support workflow productivity

Revision ID: d7a1c5e9f203
Revises: f9a2b3c4d5e6
Create Date: 2026-07-21 09:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7a1c5e9f203"
down_revision: str | None = "f9a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("queue_id", sa.String(length=100), nullable=True))
    op.add_column(
        "conversations",
        sa.Column("priority", sa.String(length=30), server_default="normal", nullable=False),
    )
    op.add_column(
        "conversations", sa.Column("tags", sa.JSON(), server_default="[]", nullable=False)
    )
    op.add_column(
        "conversations", sa.Column("first_response_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "conversations",
        sa.Column("first_human_response_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "conversations", sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(op.f("ix_conversations_queue_id"), "conversations", ["queue_id"])
    op.create_index(op.f("ix_conversations_priority"), "conversations", ["priority"])

    op.create_table(
        "support_queues",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("queue_id", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_support_queues")),
        sa.UniqueConstraint("tenant_id", "name", name="uq_support_queues_tenant_name"),
        sa.UniqueConstraint("tenant_id", "queue_id", name="uq_support_queues_tenant_queue"),
    )
    for column in ("tenant_id", "queue_id", "status"):
        op.create_index(op.f(f"ix_support_queues_{column}"), "support_queues", [column])

    op.create_table(
        "canned_replies",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("reply_id", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("shortcut", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_canned_replies")),
        sa.UniqueConstraint("tenant_id", "reply_id", name="uq_canned_replies_tenant_reply"),
        sa.UniqueConstraint("tenant_id", "shortcut", name="uq_canned_replies_tenant_shortcut"),
        )
    op.execute(
        sa.text(
            """
            INSERT INTO support_queues
                (tenant_id, queue_id, name, description, is_default, status, created_at, updated_at)
            SELECT DISTINCT tenant_id, 'general', '通用客服', '默认客服队列', true, 'active',
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            FROM support_sites
            """
        )
    )
    for column in ("tenant_id", "reply_id", "shortcut", "status"):
        op.create_index(op.f(f"ix_canned_replies_{column}"), "canned_replies", [column])

    op.add_column("handoff_requests", sa.Column("user_intent", sa.String(100), nullable=True))
    op.add_column("handoff_requests", sa.Column("unresolved_question", sa.Text(), nullable=True))
    op.add_column("handoff_requests", sa.Column("ai_attempt", sa.Text(), nullable=True))
    op.add_column("handoff_requests", sa.Column("suggested_next_action", sa.Text(), nullable=True))
    op.add_column("handoff_requests", sa.Column("reply_draft", sa.Text(), nullable=True))
    op.add_column("handoff_requests", sa.Column("queue_id", sa.String(100), nullable=True))
    op.create_index(op.f("ix_handoff_requests_queue_id"), "handoff_requests", ["queue_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_handoff_requests_queue_id"), table_name="handoff_requests")
    for column in (
        "queue_id",
        "reply_draft",
        "suggested_next_action",
        "ai_attempt",
        "unresolved_question",
        "user_intent",
    ):
        op.drop_column("handoff_requests", column)
    op.drop_table("canned_replies")
    op.drop_table("support_queues")
    op.drop_index(op.f("ix_conversations_priority"), table_name="conversations")
    op.drop_index(op.f("ix_conversations_queue_id"), table_name="conversations")
    for column in (
        "resolved_at",
        "first_human_response_at",
        "first_response_at",
        "tags",
        "priority",
        "queue_id",
    ):
        op.drop_column("conversations", column)
