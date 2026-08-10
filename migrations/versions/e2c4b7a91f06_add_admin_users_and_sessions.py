"""add admin users and sessions

Revision ID: e2c4b7a91f06
Revises: d18a8f3c62e4
Create Date: 2026-07-15 18:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e2c4b7a91f06"
down_revision: Union[str, None] = "d18a8f3c62e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "admin_users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("user_id", sa.String(length=100), nullable=False),
        sa.Column("username", sa.String(length=200), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("password_hash", sa.String(length=500), nullable=False),
        sa.Column("roles", sa.JSON(), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_users")),
        sa.UniqueConstraint(
            "tenant_id", "user_id", name=op.f("uq_admin_users_tenant_id_user_id")
        ),
        sa.UniqueConstraint(
            "tenant_id", "username", name=op.f("uq_admin_users_tenant_id_username")
        ),
    )
    op.create_index(op.f("ix_admin_users_tenant_id"), "admin_users", ["tenant_id"])
    op.create_index(op.f("ix_admin_users_user_id"), "admin_users", ["user_id"])
    op.create_index(op.f("ix_admin_users_username"), "admin_users", ["username"])
    op.create_index(op.f("ix_admin_users_status"), "admin_users", ["status"])

    op.create_table(
        "admin_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("session_id", sa.String(length=100), nullable=False),
        sa.Column("user_id", sa.String(length=100), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["admin_users.tenant_id", "admin_users.user_id"],
            name=op.f("fk_admin_sessions_tenant_id_user_id_admin_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_sessions")),
        sa.UniqueConstraint(
            "tenant_id",
            "session_id",
            name=op.f("uq_admin_sessions_tenant_id_session_id"),
        ),
        sa.UniqueConstraint("token_hash", name=op.f("uq_admin_sessions_token_hash")),
    )
    op.create_index(op.f("ix_admin_sessions_tenant_id"), "admin_sessions", ["tenant_id"])
    op.create_index(op.f("ix_admin_sessions_session_id"), "admin_sessions", ["session_id"])
    op.create_index(op.f("ix_admin_sessions_user_id"), "admin_sessions", ["user_id"])
    op.create_index(op.f("ix_admin_sessions_token_hash"), "admin_sessions", ["token_hash"])
    op.create_index(op.f("ix_admin_sessions_expires_at"), "admin_sessions", ["expires_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_admin_sessions_expires_at"), table_name="admin_sessions")
    op.drop_index(op.f("ix_admin_sessions_token_hash"), table_name="admin_sessions")
    op.drop_index(op.f("ix_admin_sessions_user_id"), table_name="admin_sessions")
    op.drop_index(op.f("ix_admin_sessions_session_id"), table_name="admin_sessions")
    op.drop_index(op.f("ix_admin_sessions_tenant_id"), table_name="admin_sessions")
    op.drop_table("admin_sessions")
    op.drop_index(op.f("ix_admin_users_status"), table_name="admin_users")
    op.drop_index(op.f("ix_admin_users_username"), table_name="admin_users")
    op.drop_index(op.f("ix_admin_users_user_id"), table_name="admin_users")
    op.drop_index(op.f("ix_admin_users_tenant_id"), table_name="admin_users")
    op.drop_table("admin_users")
