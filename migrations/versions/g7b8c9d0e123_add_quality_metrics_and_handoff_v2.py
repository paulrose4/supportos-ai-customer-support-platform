"""add quality metrics, auto resolution, and handoff context v2

Revision ID: g7b8c9d0e123
Revises: f6a7b8c9d012
Create Date: 2026-07-22 03:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "g7b8c9d0e123"
down_revision: str | None = "f6a7b8c9d012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_runs", sa.Column("request_received_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "agent_runs", sa.Column("response_sent_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("agent_runs", sa.Column("response_latency_ms", sa.Integer(), nullable=True))
    op.add_column("agent_runs", sa.Column("request_route", sa.String(length=120), nullable=True))
    op.create_index(
        "ix_agent_runs_request_received_at", "agent_runs", ["request_received_at"], unique=False
    )
    op.create_index(
        "ix_agent_runs_response_sent_at", "agent_runs", ["response_sent_at"], unique=False
    )
    op.create_index("ix_agent_runs_request_route", "agent_runs", ["request_route"], unique=False)

    op.add_column(
        "conversations",
        sa.Column("auto_resolution_eligible_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "conversations", sa.Column("auto_resolved_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "conversations", sa.Column("reopened_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "conversations", sa.Column("resolution_source", sa.String(length=50), nullable=True)
    )
    op.create_index(
        "ix_conversations_auto_resolution_eligible_at",
        "conversations",
        ["auto_resolution_eligible_at"],
        unique=False,
    )
    op.create_index(
        "ix_conversations_resolution_source",
        "conversations",
        ["resolution_source"],
        unique=False,
    )

    op.add_column(
        "handoff_requests",
        sa.Column("context_schema_version", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "handoff_requests", sa.Column("customer_language", sa.String(length=20), nullable=True)
    )
    op.add_column(
        "handoff_requests", sa.Column("customer_region", sa.String(length=20), nullable=True)
    )
    op.add_column(
        "handoff_requests", sa.Column("identity_status", sa.String(length=50), nullable=True)
    )
    op.add_column(
        "handoff_requests",
        sa.Column("product_ids", sa.JSON(), server_default=sa.text("'[]'::json"), nullable=False),
    )
    op.add_column(
        "handoff_requests", sa.Column("order_id", sa.String(length=100), nullable=True)
    )
    op.add_column(
        "handoff_requests",
        sa.Column(
            "confirmed_fields", sa.JSON(), server_default=sa.text("'[]'::json"), nullable=False
        ),
    )
    op.add_column(
        "handoff_requests", sa.Column("customer_sentiment", sa.String(length=30), nullable=True)
    )
    op.add_column("handoff_requests", sa.Column("customer_request", sa.Text(), nullable=True))
    op.add_column(
        "handoff_requests",
        sa.Column("commitment_deadline", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    for column in (
        "commitment_deadline",
        "customer_request",
        "customer_sentiment",
        "confirmed_fields",
        "order_id",
        "product_ids",
        "identity_status",
        "customer_region",
        "customer_language",
        "context_schema_version",
    ):
        op.drop_column("handoff_requests", column)

    op.drop_index("ix_conversations_resolution_source", table_name="conversations")
    op.drop_index("ix_conversations_auto_resolution_eligible_at", table_name="conversations")
    for column in (
        "resolution_source",
        "reopened_at",
        "auto_resolved_at",
        "auto_resolution_eligible_at",
    ):
        op.drop_column("conversations", column)

    op.drop_index("ix_agent_runs_request_route", table_name="agent_runs")
    op.drop_index("ix_agent_runs_response_sent_at", table_name="agent_runs")
    op.drop_index("ix_agent_runs_request_received_at", table_name="agent_runs")
    for column in (
        "request_route",
        "response_latency_ms",
        "response_sent_at",
        "request_received_at",
    ):
        op.drop_column("agent_runs", column)
