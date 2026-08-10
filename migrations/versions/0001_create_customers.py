"""create tenant-scoped customers table

Revision ID: 0001
Revises:
Create Date: 2026-07-14
"""

from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("customer_id", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_customers"),
        sa.UniqueConstraint("tenant_id", "customer_id", name="uq_customers_tenant_customer"),
    )
    op.create_index("ix_customers_tenant_id", "customers", ["tenant_id"])
    op.create_index("ix_customers_customer_id", "customers", ["customer_id"])


def downgrade() -> None:
    op.drop_index("ix_customers_customer_id", table_name="customers")
    op.drop_index("ix_customers_tenant_id", table_name="customers")
    op.drop_table("customers")