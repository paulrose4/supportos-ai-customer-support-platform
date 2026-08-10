"""enforce tenant-scoped site bindings for support queues"""

from collections.abc import Sequence

from alembic import op

revision: str = "y5b6c7d8e9f0"
down_revision: str | None = "x4a5b6c7d8e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_foreign_key(
        "fk_support_queues_tenant_site",
        "support_queues",
        "support_sites",
        ["tenant_id", "site_id"],
        ["tenant_id", "site_id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("fk_support_queues_tenant_site", "support_queues", type_="foreignkey")
