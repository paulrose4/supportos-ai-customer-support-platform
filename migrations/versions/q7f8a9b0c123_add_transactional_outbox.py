"""add transactional realtime outbox

Revision ID: q7f8a9b0c123
Revises: p6e7f8a9b012
Create Date: 2026-07-31 05:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "q7f8a9b0c123"
down_revision: str | None = "p6e7f8a9b012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("event_id", sa.String(length=100), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("correlation_id", sa.String(length=100), nullable=True),
        sa.Column("resource_type", sa.String(length=100), nullable=False),
        sa.Column("resource_id", sa.String(length=100), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=30), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "event_id"),
    )
    for column in ("tenant_id", "event_id", "event_type", "status", "available_at", "created_at"):
        op.create_index(f"ix_outbox_events_{column}", "outbox_events", [column])
    op.execute("ALTER TABLE outbox_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE outbox_events FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON outbox_events
        USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))
        """
    )
    op.execute(
        """
        CREATE FUNCTION enqueue_audit_outbox_event() RETURNS trigger AS $$
        BEGIN
          INSERT INTO outbox_events (
            tenant_id, event_id, event_type, correlation_id, resource_type,
            resource_id, payload, status, attempts, available_at, created_at
          ) VALUES (
            NEW.tenant_id, NEW.event_id, NEW.event_type, NEW.correlation_id,
            NEW.resource_type, NEW.resource_id, NEW.details, 'pending', 0,
            NEW.created_at, NEW.created_at
          ) ON CONFLICT (tenant_id, event_id) DO NOTHING;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_event_transactional_outbox
        AFTER INSERT ON audit_events
        FOR EACH ROW EXECUTE FUNCTION enqueue_audit_outbox_event();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_event_transactional_outbox ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS enqueue_audit_outbox_event()")
    op.drop_table("outbox_events")
