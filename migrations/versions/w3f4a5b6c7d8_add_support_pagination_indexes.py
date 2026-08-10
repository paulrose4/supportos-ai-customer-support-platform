"""add indexes for tenant-scoped support pagination

Revision ID: w3f4a5b6c7d8
Revises: v2e3f4a5b6c7
"""

from collections.abc import Sequence

from alembic import op

revision: str = "w3f4a5b6c7d8"
down_revision: str | None = "v2e3f4a5b6c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_conversations_tenant_site_updated_id",
        "conversations",
        ["tenant_id", "site_id", "updated_at", "id"],
    )
    op.create_index(
        "ix_conversations_tenant_customer_updated",
        "conversations",
        ["tenant_id", "customer_id", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_conversations_tenant_customer_updated", table_name="conversations")
    op.drop_index("ix_conversations_tenant_site_updated_id", table_name="conversations")
