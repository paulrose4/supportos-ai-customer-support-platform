"""allow audited global synchronization events in the outbox

Revision ID: g8c9d0e1f2a3
Revises: f7b8c9d0e1f2
"""

from collections.abc import Sequence

from alembic import op

revision: str = "g8c9d0e1f2a3"
down_revision: str | None = "f7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_POLICY = """
CREATE POLICY tenant_isolation ON outbox_events
USING (
    tenant_id = NULLIF(current_setting('app.tenant_id', true), '')
    OR (
        tenant_id = '__global__'
        AND current_setting('app.global_access', true) = 'on'
    )
)
WITH CHECK (
    tenant_id = NULLIF(current_setting('app.tenant_id', true), '')
    OR (
        tenant_id = '__global__'
        AND current_setting('app.global_access', true) = 'on'
    )
)
"""


def upgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON outbox_events")
    op.execute(_POLICY)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON outbox_events")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON outbox_events
        USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))
        """
    )
