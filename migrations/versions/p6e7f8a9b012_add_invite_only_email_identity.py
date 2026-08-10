"""add invite-only email identity

Revision ID: p6e7f8a9b012
Revises: o5d6e7f8a901
Create Date: 2026-07-29 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "p6e7f8a9b012"
down_revision: str | None = "o5d6e7f8a901"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "email_identities",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("identity_id", sa.String(length=100), nullable=False),
        sa.Column("user_id", sa.String(length=100), nullable=False),
        sa.Column("normalized_email", sa.String(length=320), nullable=False),
        sa.Column("display_email", sa.String(length=320), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("identity_id"),
        sa.UniqueConstraint("normalized_email"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_email_identities_identity_id", "email_identities", ["identity_id"])
    op.create_index("ix_email_identities_user_id", "email_identities", ["user_id"])
    op.create_index(
        "ix_email_identities_normalized_email",
        "email_identities",
        ["normalized_email"],
    )
    op.create_index("ix_email_identities_status", "email_identities", ["status"])

    op.create_table(
        "password_credentials",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=100), nullable=False),
        sa.Column("password_hash", sa.String(length=500), nullable=False),
        sa.Column("password_version", sa.Integer(), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index(
        "ix_password_credentials_user_id",
        "password_credentials",
        ["user_id"],
    )

    op.create_table(
        "tenant_invitations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("invitation_id", sa.String(length=100), nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("normalized_email", sa.String(length=320), nullable=False),
        sa.Column("display_email", sa.String(length=320), nullable=False),
        sa.Column("roles", sa.JSON(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("redeemed_by", sa.String(length=100), nullable=True),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.String(length=100), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("invitation_id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        "ix_tenant_invitations_invitation_id",
        "tenant_invitations",
        ["invitation_id"],
    )
    op.create_index("ix_tenant_invitations_tenant_id", "tenant_invitations", ["tenant_id"])
    op.create_index(
        "ix_tenant_invitations_normalized_email",
        "tenant_invitations",
        ["normalized_email"],
    )
    op.create_index("ix_tenant_invitations_token_hash", "tenant_invitations", ["token_hash"])
    op.create_index("ix_tenant_invitations_status", "tenant_invitations", ["status"])
    op.create_index(
        "ix_tenant_invitations_expires_at",
        "tenant_invitations",
        ["expires_at"],
    )

    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("reset_id", sa.String(length=100), nullable=False),
        sa.Column("user_id", sa.String(length=100), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reset_id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        "ix_password_reset_tokens_reset_id",
        "password_reset_tokens",
        ["reset_id"],
    )
    op.create_index(
        "ix_password_reset_tokens_user_id",
        "password_reset_tokens",
        ["user_id"],
    )
    op.create_index(
        "ix_password_reset_tokens_token_hash",
        "password_reset_tokens",
        ["token_hash"],
    )
    op.create_index(
        "ix_password_reset_tokens_expires_at",
        "password_reset_tokens",
        ["expires_at"],
    )

    op.create_table(
        "email_login_throttles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("email_hash", sa.String(length=64), nullable=False),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_failed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_fingerprint", "email_hash"),
    )
    op.create_index(
        "ix_email_login_throttles_source_fingerprint",
        "email_login_throttles",
        ["source_fingerprint"],
    )
    op.create_index(
        "ix_email_login_throttles_email_hash",
        "email_login_throttles",
        ["email_hash"],
    )
    op.create_index(
        "ix_email_login_throttles_locked_until",
        "email_login_throttles",
        ["locked_until"],
    )

    op.add_column(
        "login_completion_grants",
        sa.Column(
            "authentication_method",
            sa.String(length=40),
            nullable=False,
            server_default="dingtalk",
        ),
    )
    op.alter_column(
        "login_completion_grants",
        "authentication_method",
        server_default=None,
    )


def downgrade() -> None:
    op.drop_column("login_completion_grants", "authentication_method")
    op.drop_table("email_login_throttles")
    op.drop_table("password_reset_tokens")
    op.drop_table("tenant_invitations")
    op.drop_table("password_credentials")
    op.drop_table("email_identities")
