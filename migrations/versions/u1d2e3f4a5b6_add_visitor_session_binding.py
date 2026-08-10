"""bind anonymous conversations to public widget visitor sessions

Revision ID: u1d2e3f4a5b6
Revises: t0c1d2e3f456
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "u1d2e3f4a5b6"
down_revision: str | None = "t0c1d2e3f456"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("visitor_session_id", sa.String(100), nullable=True))
    op.create_index("ix_conversations_visitor_session_id", "conversations", ["visitor_session_id"])


def downgrade() -> None:
    op.drop_index("ix_conversations_visitor_session_id", table_name="conversations")
    op.drop_column("conversations", "visitor_session_id")
