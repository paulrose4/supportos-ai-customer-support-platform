"""add widget site credentials

Revision ID: a4b8d6e2c901
Revises: f3a1c9d4b702
Create Date: 2026-07-16 10:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a4b8d6e2c901"
down_revision: str | None = "f3a1c9d4b702"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "widget_site_credentials",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("site_id", sa.String(length=100), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("key_prefix", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id"],
            ["support_sites.tenant_id", "support_sites.site_id"],
            name=op.f("fk_widget_site_credentials_tenant_id_site_id_support_sites"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_widget_site_credentials")),
        sa.UniqueConstraint("key_hash", name=op.f("uq_widget_site_credentials_key_hash")),
        sa.UniqueConstraint(
            "tenant_id",
            "site_id",
            name=op.f("uq_widget_site_credentials_tenant_id_site_id"),
        ),
    )
    op.create_index(
        op.f("ix_widget_site_credentials_tenant_id"),
        "widget_site_credentials",
        ["tenant_id"],
    )
    op.create_index(
        op.f("ix_widget_site_credentials_site_id"),
        "widget_site_credentials",
        ["site_id"],
    )
    op.create_index(
        op.f("ix_widget_site_credentials_key_hash"),
        "widget_site_credentials",
        ["key_hash"],
    )
    op.create_index(
        op.f("ix_widget_site_credentials_status"),
        "widget_site_credentials",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_widget_site_credentials_status"), table_name="widget_site_credentials")
    op.drop_index(op.f("ix_widget_site_credentials_key_hash"), table_name="widget_site_credentials")
    op.drop_index(op.f("ix_widget_site_credentials_site_id"), table_name="widget_site_credentials")
    op.drop_index(
        op.f("ix_widget_site_credentials_tenant_id"), table_name="widget_site_credentials"
    )
    op.drop_table("widget_site_credentials")
