"""add customer experience workflows

Revision ID: f5c3a8d1e720
Revises: e4b2d7c8a910
Create Date: 2026-07-21 11:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f5c3a8d1e720"
down_revision: str | None = "e4b2d7c8a910"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "widget_config_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("site_id", sa.String(length=100), nullable=False),
        sa.Column("version_id", sa.String(length=100), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id"],
            ["support_sites.tenant_id", "support_sites.site_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_widget_config_versions")),
        sa.UniqueConstraint("tenant_id", "site_id", "version_number"),
        sa.UniqueConstraint("tenant_id", "version_id"),
    )
    for column in ("tenant_id", "site_id", "version_id", "status"):
        op.create_index(
            op.f(f"ix_widget_config_versions_{column}"),
            "widget_config_versions",
            [column],
        )
    op.execute(
        sa.text(
            """
            INSERT INTO widget_config_versions
                (tenant_id, site_id, version_id, version_number, status, config,
                 created_by, created_at, published_at)
            SELECT tenant_id, site_id, 'initial-' || site_id, 1, 'published',
                json_build_object(
                    'welcome_message', CASE WHEN primary_language LIKE 'zh%'
                        THEN '您好！今天有什么可以帮您？'
                        ELSE 'Hello! How can I help you today?' END,
                    'online_message', '客服在线',
                    'offline_message', '当前为非工作时间，请留言，我们会尽快回复。',
                    'business_timezone', 'Asia/Shanghai',
                    'business_hours', json_build_object(
                        'mon', '09:00-18:00', 'tue', '09:00-18:00',
                        'wed', '09:00-18:00', 'thu', '09:00-18:00',
                        'fri', '09:00-18:00'),
                    'holidays', json_build_array(),
                    'offline_form_enabled', true,
                    'primary_color', '#2563eb',
                    'position', 'right',
                    'agent_name', '在线客服',
                    'agent_avatar_url', null,
                    'mobile_enabled', true,
                    'default_language', primary_language,
                    'handoff_timeout_seconds', 120,
                    'csat_enabled', true),
                'system-migration', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            FROM support_sites
            """
        )
    )

    op.create_table(
        "automation_rules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("rule_id", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("conditions", sa.JSON(), nullable=False),
        sa.Column("actions", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_automation_rules")),
        sa.UniqueConstraint("tenant_id", "rule_id"),
    )
    for column in ("tenant_id", "rule_id", "enabled"):
        op.create_index(op.f(f"ix_automation_rules_{column}"), "automation_rules", [column])

    op.create_table(
        "automation_rule_executions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("execution_id", sa.String(length=100), nullable=False),
        sa.Column("rule_id", sa.String(length=100), nullable=False),
        sa.Column("conversation_id", sa.String(length=100), nullable=False),
        sa.Column("matched", sa.Boolean(), nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("actions_applied", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_automation_rule_executions")),
        sa.UniqueConstraint("tenant_id", "execution_id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key"),
    )
    for column in ("tenant_id", "execution_id", "rule_id", "conversation_id", "matched", "occurred_at"):
        op.create_index(
            op.f(f"ix_automation_rule_executions_{column}"),
            "automation_rule_executions",
            [column],
        )

    op.create_table(
        "satisfaction_ratings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("rating_id", sa.String(length=100), nullable=False),
        sa.Column("site_id", sa.String(length=100), nullable=False),
        sa.Column("conversation_id", sa.String(length=100), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_satisfaction_ratings")),
        sa.UniqueConstraint("tenant_id", "conversation_id"),
        sa.UniqueConstraint("tenant_id", "rating_id"),
    )
    for column in ("tenant_id", "rating_id", "site_id", "conversation_id", "created_at"):
        op.create_index(
            op.f(f"ix_satisfaction_ratings_{column}"), "satisfaction_ratings", [column]
        )

    op.create_table(
        "knowledge_gaps",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("gap_id", sa.String(length=100), nullable=False),
        sa.Column("conversation_id", sa.String(length=100), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_by", sa.String(length=100), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_gaps")),
        sa.UniqueConstraint("tenant_id", "gap_id"),
    )
    for column in ("tenant_id", "gap_id", "conversation_id", "category", "status", "created_at"):
        op.create_index(op.f(f"ix_knowledge_gaps_{column}"), "knowledge_gaps", [column])


def downgrade() -> None:
    op.drop_table("knowledge_gaps")
    op.drop_table("satisfaction_ratings")
    op.drop_table("automation_rule_executions")
    op.drop_table("automation_rules")
    op.drop_table("widget_config_versions")
