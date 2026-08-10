"""version-scope knowledge chunk manifest identifiers

Revision ID: f6a7b8c9d012
Revises: f5c3a8d1e720
Create Date: 2026-07-22 01:15:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f6a7b8c9d012"
down_revision: str | None = "f5c3a8d1e720"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_knowledge_chunk_manifests_tenant_id_chunk_id",
        "knowledge_chunk_manifests",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_knowledge_chunk_manifests_tenant_version_chunk",
        "knowledge_chunk_manifests",
        ["tenant_id", "version_id", "chunk_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_knowledge_chunk_manifests_tenant_version_chunk",
        "knowledge_chunk_manifests",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_knowledge_chunk_manifests_tenant_id_chunk_id",
        "knowledge_chunk_manifests",
        ["tenant_id", "chunk_id"],
    )
