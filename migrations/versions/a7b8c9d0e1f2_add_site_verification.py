"""add site ownership verification state

Revision ID: a7b8c9d0e1f2
Revises: z6c7d8e9f0a1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: str | None = "z6c7d8e9f0a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("support_sites", "public_widget_registry"):
        op.add_column(
            table,
            sa.Column(
                "verification_status",
                sa.String(length=30),
                nullable=False,
                server_default="verified",
            ),
        )
        op.create_index(
            f"ix_{table}_verification_status",
            table,
            ["verification_status"],
        )
    op.add_column("support_sites", sa.Column("verification_method", sa.String(length=30)))
    op.add_column("support_sites", sa.Column("verification_token_hash", sa.String(length=64)))
    op.add_column("support_sites", sa.Column("verification_token_prefix", sa.String(length=16)))
    op.add_column(
        "support_sites",
        sa.Column("verification_expires_at", sa.DateTime(timezone=True)),
    )
    op.add_column("support_sites", sa.Column("verified_at", sa.DateTime(timezone=True)))
    op.create_index(
        "ix_support_sites_verification_token_hash",
        "support_sites",
        ["verification_token_hash"],
    )
    op.alter_column("support_sites", "verification_status", server_default=None)
    op.alter_column("public_widget_registry", "verification_status", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_support_sites_verification_token_hash", table_name="support_sites")
    for column in (
        "verified_at",
        "verification_expires_at",
        "verification_token_prefix",
        "verification_token_hash",
        "verification_method",
    ):
        op.drop_column("support_sites", column)
    for table in ("public_widget_registry", "support_sites"):
        op.drop_index(f"ix_{table}_verification_status", table_name=table)
        op.drop_column(table, "verification_status")
