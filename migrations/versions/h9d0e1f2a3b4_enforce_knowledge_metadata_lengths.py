"""enforce knowledge metadata lengths

Revision ID: h9d0e1f2a3b4
Revises: g8c9d0e1f2a3
"""

from collections.abc import Sequence

from alembic import op

revision: str = "h9d0e1f2a3b4"
down_revision: str | None = "g8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_check_constraint(
        "title_length",
        "knowledge_documents",
        "char_length(title) <= 500",
    )
    op.create_check_constraint(
        "heading_length",
        "knowledge_chunk_manifests",
        "heading IS NULL OR char_length(heading) <= 500",
    )


def downgrade() -> None:
    op.drop_constraint(
        "heading_length",
        "knowledge_chunk_manifests",
        type_="check",
    )
    op.drop_constraint(
        "title_length",
        "knowledge_documents",
        type_="check",
    )
