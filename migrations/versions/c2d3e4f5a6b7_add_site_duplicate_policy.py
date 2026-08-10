"""allow a site to override the global product duplicate policy"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c2d3e4f5a6b7"
down_revision: str | None = "c1d2e3f4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "support_sites",
        sa.Column("duplicate_product_policy", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "support_sites",
        sa.Column("duplicate_product_order", sa.String(length=50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("support_sites", "duplicate_product_order")
    op.drop_column("support_sites", "duplicate_product_policy")
