"""harden public widget sessions, quotas, and message cursors

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: str | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "public_widget_registry",
        sa.Column("auth_version", sa.Integer(), nullable=False, server_default="1"),
    )

    op.create_table(
        "site_usage_daily",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("site_id", sa.String(length=100), nullable=False),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("admitted_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id"],
            ["support_sites.tenant_id", "support_sites.site_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "site_id", "usage_date"),
    )
    op.create_index("ix_site_usage_daily_tenant_id", "site_usage_daily", ["tenant_id"])
    op.create_index("ix_site_usage_daily_site_id", "site_usage_daily", ["site_id"])
    op.create_index("ix_site_usage_daily_usage_date", "site_usage_daily", ["usage_date"])

    op.create_table(
        "widget_visitor_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("site_id", sa.String(length=100), nullable=False),
        sa.Column("public_widget_id", sa.String(length=80), nullable=False),
        sa.Column("session_id", sa.String(length=100), nullable=False),
        sa.Column("origin", sa.String(length=500), nullable=False),
        sa.Column("resume_token_hash", sa.String(length=64), nullable=False),
        sa.Column("previous_resume_token_hash", sa.String(length=64)),
        sa.Column("previous_valid_until", sa.DateTime(timezone=True)),
        sa.Column("token_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id"],
            ["support_sites.tenant_id", "support_sites.site_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("resume_token_hash"),
        sa.UniqueConstraint("tenant_id", "site_id", "session_id"),
    )
    session_indexes = (
        "tenant_id",
        "site_id",
        "public_widget_id",
        "session_id",
        "resume_token_hash",
        "expires_at",
    )
    for column in session_indexes:
        op.create_index(
            f"ix_widget_visitor_sessions_{column}", "widget_visitor_sessions", [column]
        )

    op.create_index(
        "ix_messages_public_human_cursor",
        "messages",
        ["tenant_id", "conversation_id", "role", "message_type", "id"],
    )

    for table in ("site_usage_daily", "widget_visitor_sessions"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')) "
            "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))"
        )


def downgrade() -> None:
    op.drop_index("ix_messages_public_human_cursor", table_name="messages")
    for table in ("widget_visitor_sessions", "site_usage_daily"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.drop_table(table)
    op.drop_column("public_widget_registry", "auth_version")
