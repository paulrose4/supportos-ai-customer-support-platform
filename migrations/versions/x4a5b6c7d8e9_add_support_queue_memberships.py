"""add tenant-scoped support queue memberships"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "x4a5b6c7d8e9"
down_revision: str | None = "w3f4a5b6c7d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("support_queues", sa.Column("site_id", sa.String(100), nullable=True))
    op.create_index("ix_support_queues_site_id", "support_queues", ["site_id"])
    op.create_table(
        "support_queue_memberships",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(100), nullable=False),
        sa.Column("queue_id", sa.String(100), nullable=False),
        sa.Column("user_id", sa.String(100), nullable=False),
        sa.Column("role", sa.String(30), nullable=False, server_default="member"),
        sa.Column("status", sa.String(30), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "queue_id"],
            ["support_queues.tenant_id", "support_queues.queue_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "queue_id", "user_id", name="uq_support_queue_memberships_member"
        ),
    )
    op.create_index(
        "ix_support_queue_memberships_tenant_id", "support_queue_memberships", ["tenant_id"]
    )
    op.create_index(
        "ix_support_queue_memberships_queue_id", "support_queue_memberships", ["queue_id"]
    )
    op.create_index(
        "ix_support_queue_memberships_user_id", "support_queue_memberships", ["user_id"]
    )
    op.create_index("ix_support_queue_memberships_status", "support_queue_memberships", ["status"])
    op.execute("ALTER TABLE support_queue_memberships ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE support_queue_memberships FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON support_queue_memberships "
        "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')) "
        "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON support_queue_memberships")
    op.drop_index("ix_support_queue_memberships_status", table_name="support_queue_memberships")
    op.drop_index("ix_support_queue_memberships_user_id", table_name="support_queue_memberships")
    op.drop_index("ix_support_queue_memberships_queue_id", table_name="support_queue_memberships")
    op.drop_index("ix_support_queue_memberships_tenant_id", table_name="support_queue_memberships")
    op.drop_table("support_queue_memberships")
    op.drop_index("ix_support_queues_site_id", table_name="support_queues")
    op.drop_column("support_queues", "site_id")
