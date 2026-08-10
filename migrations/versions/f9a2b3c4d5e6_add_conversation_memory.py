"""add conversation memory

Revision ID: f9a2b3c4d5e6
Revises: e8f1a2b3c4d5
Create Date: 2026-07-20 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f9a2b3c4d5e6"
down_revision: str | None = "e8f1a2b3c4d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversation_memories",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("conversation_id", sa.String(length=100), nullable=False),
        sa.Column("site_id", sa.String(length=100), nullable=False),
        sa.Column("working_memory", sa.JSON(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("summarized_message_count", sa.Integer(), nullable=False),
        sa.Column("last_summarized_message_db_id", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("last_trace_id", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["conversations.tenant_id", "conversations.conversation_id"],
            name=op.f("fk_conversation_memories_tenant_id_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversation_memories")),
        sa.UniqueConstraint(
            "tenant_id",
            "conversation_id",
            name=op.f("uq_conversation_memories_tenant_id_conversation_id"),
        ),
    )
    for column in ("tenant_id", "conversation_id", "site_id", "last_trace_id"):
        op.create_index(
            op.f(f"ix_conversation_memories_{column}"),
            "conversation_memories",
            [column],
        )


def downgrade() -> None:
    for column in ("last_trace_id", "site_id", "conversation_id", "tenant_id"):
        op.drop_index(
            op.f(f"ix_conversation_memories_{column}"),
            table_name="conversation_memories",
        )
    op.drop_table("conversation_memories")
