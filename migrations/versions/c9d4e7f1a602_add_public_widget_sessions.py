"""add public widget sessions

Revision ID: c9d4e7f1a602
Revises: b7f2c9e41d30
Create Date: 2026-07-18 11:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9d4e7f1a602"
down_revision: str | None = "b7f2c9e41d30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "support_sites",
        sa.Column("public_widget_id", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "support_sites",
        sa.Column("allowed_origins", sa.JSON(), server_default=sa.text("'[]'::json"), nullable=False),
    )
    op.add_column(
        "support_sites",
        sa.Column(
            "widget_daily_message_limit",
            sa.Integer(),
            server_default="500",
            nullable=False,
        ),
    )
    op.execute(
        """
        UPDATE support_sites
        SET public_widget_id = 'site_pub_' || substr(md5(tenant_id || ':' || site_id), 1, 24)
        """
    )
    op.execute(
        """
        UPDATE support_sites
        SET allowed_origins = json_build_array(
            regexp_replace(base_url, '^(https?://[^/]+).*$', '\\1')
        )
        WHERE base_url <> '' AND base_url ~ '^https?://[^/]+'
        """
    )
    op.alter_column("support_sites", "public_widget_id", nullable=False)
    op.alter_column("support_sites", "allowed_origins", server_default=None)
    op.alter_column("support_sites", "widget_daily_message_limit", server_default=None)
    op.create_index(
        op.f("ix_support_sites_public_widget_id"),
        "support_sites",
        ["public_widget_id"],
        unique=True,
    )
    op.create_table(
        "widget_message_admissions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("site_id", sa.String(length=100), nullable=False),
        sa.Column("request_id", sa.String(length=100), nullable=False),
        sa.Column("admitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id"],
            ["support_sites.tenant_id", "support_sites.site_id"],
            name=op.f("fk_widget_message_admissions_tenant_id_site_id_support_sites"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_widget_message_admissions")),
        sa.UniqueConstraint(
            "tenant_id",
            "site_id",
            "request_id",
            name=op.f("uq_widget_message_admissions_tenant_id_site_id_request_id"),
        ),
    )
    op.create_index(
        op.f("ix_widget_message_admissions_tenant_id"),
        "widget_message_admissions",
        ["tenant_id"],
    )
    op.create_index(
        op.f("ix_widget_message_admissions_site_id"),
        "widget_message_admissions",
        ["site_id"],
    )
    op.create_index(
        op.f("ix_widget_message_admissions_request_id"),
        "widget_message_admissions",
        ["request_id"],
    )
    op.create_index(
        op.f("ix_widget_message_admissions_admitted_at"),
        "widget_message_admissions",
        ["admitted_at"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_widget_message_admissions_admitted_at"),
        table_name="widget_message_admissions",
    )
    op.drop_index(
        op.f("ix_widget_message_admissions_request_id"),
        table_name="widget_message_admissions",
    )
    op.drop_index(
        op.f("ix_widget_message_admissions_site_id"),
        table_name="widget_message_admissions",
    )
    op.drop_index(
        op.f("ix_widget_message_admissions_tenant_id"),
        table_name="widget_message_admissions",
    )
    op.drop_table("widget_message_admissions")
    op.drop_index(op.f("ix_support_sites_public_widget_id"), table_name="support_sites")
    op.drop_column("support_sites", "widget_daily_message_limit")
    op.drop_column("support_sites", "allowed_origins")
    op.drop_column("support_sites", "public_widget_id")
