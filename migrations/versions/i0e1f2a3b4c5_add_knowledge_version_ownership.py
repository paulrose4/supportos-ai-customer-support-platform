"""add immutable ownership and lifecycle to website knowledge versions

Revision ID: i0e1f2a3b4c5
Revises: h9d0e1f2a3b4
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "i0e1f2a3b4c5"
down_revision: str | None = "h9d0e1f2a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_document_versions",
        sa.Column("snapshot_id", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "knowledge_document_versions",
        sa.Column(
            "lifecycle_state",
            sa.String(length=30),
            nullable=False,
            server_default="draft",
        ),
    )
    op.execute(
        """
        UPDATE knowledge_document_versions
        SET snapshot_id = NULLIF(metadata_payload ->> 'snapshot_id', ''),
            lifecycle_state = CASE
                WHEN status = 'discarded' OR index_status IN ('discarded', 'excluded')
                    THEN 'discarded'
                WHEN index_status = 'active' THEN 'active'
                WHEN index_status IN ('indexed', 'staged') THEN 'publishable'
                ELSE 'draft'
            END
        WHERE snapshot_id IS NULL
        """
    )
    op.create_index(
        "ix_knowledge_document_versions_snapshot_id",
        "knowledge_document_versions",
        ["tenant_id", "snapshot_id"],
    )
    op.create_index(
        "ix_knowledge_document_versions_lifecycle_state",
        "knowledge_document_versions",
        ["tenant_id", "lifecycle_state"],
    )
    op.execute(
        "ALTER TABLE knowledge_document_versions "
        "DROP CONSTRAINT IF EXISTS "
        "uq_knowledge_document_versions_tenant_id_document_id_content_hash"
    )
    op.create_index(
        "ix_knowledge_document_versions_document_content_hash",
        "knowledge_document_versions",
        ["tenant_id", "document_id", "content_hash"],
    )
    op.create_check_constraint(
        "knowledge_version_lifecycle_state",
        "knowledge_document_versions",
        "lifecycle_state IN ('draft', 'staged', 'indexed', 'publishable', 'active', 'discarded')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "knowledge_version_lifecycle_state",
        "knowledge_document_versions",
        type_="check",
    )
    op.drop_index(
        "ix_knowledge_document_versions_document_content_hash",
        table_name="knowledge_document_versions",
    )
    op.drop_index(
        "ix_knowledge_document_versions_lifecycle_state",
        table_name="knowledge_document_versions",
    )
    op.drop_index(
        "ix_knowledge_document_versions_snapshot_id",
        table_name="knowledge_document_versions",
    )
    op.drop_column("knowledge_document_versions", "lifecycle_state")
    op.drop_column("knowledge_document_versions", "snapshot_id")
    op.create_unique_constraint(
        "uq_knowledge_document_versions_tenant_id_document_id_content_hash",
        "knowledge_document_versions",
        ["tenant_id", "document_id", "content_hash"],
    )
