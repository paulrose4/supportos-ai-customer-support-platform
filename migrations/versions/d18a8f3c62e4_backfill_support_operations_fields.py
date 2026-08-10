"""backfill support operations fields

Revision ID: d18a8f3c62e4
Revises: c71d3d7f4b10
Create Date: 2026-07-15 16:30:00
"""
from typing import Sequence, Union

from alembic import op

revision: str = "d18a8f3c62e4"
down_revision: Union[str, None] = "c71d3d7f4b10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE conversations AS conversation
        SET
            site_id = COALESCE(conversation.site_id, 'default-site'),
            identity_verified = (conversation.customer_id IS NOT NULL),
            last_message_at = COALESCE(
                conversation.last_message_at,
                (
                    SELECT MAX(message.created_at)
                    FROM messages AS message
                    WHERE message.tenant_id = conversation.tenant_id
                      AND message.conversation_id = conversation.conversation_id
                ),
                conversation.updated_at
            ),
            risk_level = GREATEST(
                conversation.risk_level,
                COALESCE(
                    (
                        SELECT MAX(agent_run.risk_level)
                        FROM agent_runs AS agent_run
                        WHERE agent_run.tenant_id = conversation.tenant_id
                          AND agent_run.conversation_id = conversation.conversation_id
                    ),
                    0
                )
            )
        """
    )
    op.execute(
        """
        UPDATE conversations AS conversation
        SET status = 'waiting_human', ownership_mode = 'queued'
        WHERE conversation.ownership_mode <> 'human'
          AND EXISTS (
              SELECT 1
              FROM handoff_requests AS handoff
              WHERE handoff.tenant_id = conversation.tenant_id
                AND handoff.conversation_id = conversation.conversation_id
                AND handoff.status = 'pending'
          )
        """
    )
    op.execute(
        """
        UPDATE conversations AS conversation
        SET
            status = 'open',
            ownership_mode = 'human',
            assigned_agent_id = handoff.assigned_agent_id
        FROM handoff_requests AS handoff
        WHERE handoff.tenant_id = conversation.tenant_id
          AND handoff.conversation_id = conversation.conversation_id
          AND handoff.status = 'assigned'
          AND handoff.assigned_agent_id IS NOT NULL
        """
    )


def downgrade() -> None:
    # Historical backfills are intentionally not erased on downgrade.
    pass
