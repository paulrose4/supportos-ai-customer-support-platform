"""harden tenant experience attribution and releases

Revision ID: c0d1e2f3a4b5
Revises: b8c9d0e1f2a3
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c0d1e2f3a4b5"
down_revision: str | None = "b8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "issue_outcome_episodes",
        sa.Column("actor_cohort_hash", sa.String(64), nullable=False, server_default=""),
    )
    op.create_index(
        "ix_issue_outcome_episodes_actor_cohort_hash",
        "issue_outcome_episodes",
        ["actor_cohort_hash"],
    )
    op.add_column(
        "issue_outcome_episodes",
        sa.Column("resolution_status", sa.String(30), nullable=False, server_default="open"),
    )
    op.add_column(
        "issue_outcome_episodes",
        sa.Column("learning_outcome", sa.String(50), nullable=False, server_default="unknown"),
    )
    op.add_column(
        "issue_outcome_episodes",
        sa.Column("outcome_source", sa.String(30), nullable=False, server_default="unknown"),
    )
    op.add_column(
        "issue_outcome_episodes",
        sa.Column("outcome_conflict_status", sa.String(30), nullable=False, server_default="none"),
    )
    op.execute(
        "UPDATE issue_outcome_episodes SET learning_outcome = status, "
        "resolution_status = CASE "
        "WHEN status IN ('successful_self_service', 'successful_human_resolution', "
        "'correct_policy_handoff', 'avoidable_handoff') THEN 'resolved' "
        "WHEN status = 'failed_answer' THEN 'failed' "
        "WHEN status = 'reopened' THEN 'reopened' "
        "WHEN status = 'censored' THEN 'censored' "
        "WHEN status = 'observing' THEN 'observing' ELSE 'open' END"
    )
    for column in (
        "resolution_status",
        "learning_outcome",
        "outcome_source",
        "outcome_conflict_status",
    ):
        op.create_index(f"ix_issue_outcome_episodes_{column}", "issue_outcome_episodes", [column])

    for name, default in (
        ("eligible", True),
        ("selected", True),
        ("influenced", False),
        ("outcome_attributed", False),
    ):
        op.add_column(
            "experience_memory_usages",
            sa.Column(
                name,
                sa.Boolean(),
                nullable=False,
                server_default=sa.true() if default else sa.false(),
            ),
        )
    op.create_index(
        "ix_experience_memory_usages_outcome_attributed",
        "experience_memory_usages",
        ["outcome_attributed"],
    )

    op.add_column(
        "tenant_case_memories",
        sa.Column("source_actor_cohort_hash", sa.String(64), nullable=False, server_default=""),
    )
    op.create_index(
        "ix_tenant_case_memories_source_actor_cohort_hash",
        "tenant_case_memories",
        ["source_actor_cohort_hash"],
    )

    op.add_column(
        "experience_releases", sa.Column("manifest", sa.JSON(), nullable=False, server_default="{}")
    )
    op.add_column(
        "experience_releases", sa.Column("dataset_version", sa.String(100), nullable=True)
    )
    op.add_column(
        "experience_releases",
        sa.Column("guardrail_report", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "experience_releases", sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("experience_experiments", sa.Column("issue_id", sa.String(100), nullable=True))
    op.create_index("ix_experience_experiments_issue_id", "experience_experiments", ["issue_id"])


def downgrade() -> None:
    op.drop_index("ix_experience_experiments_issue_id", table_name="experience_experiments")
    op.drop_column("experience_experiments", "issue_id")
    for column in ("paused_at", "guardrail_report", "dataset_version", "manifest"):
        op.drop_column("experience_releases", column)
    op.drop_index(
        "ix_experience_memory_usages_outcome_attributed",
        table_name="experience_memory_usages",
    )
    for column in ("outcome_attributed", "influenced", "selected", "eligible"):
        op.drop_column("experience_memory_usages", column)
    op.drop_index(
        "ix_tenant_case_memories_source_actor_cohort_hash",
        table_name="tenant_case_memories",
    )
    op.drop_column("tenant_case_memories", "source_actor_cohort_hash")
    for column in (
        "outcome_conflict_status",
        "outcome_source",
        "learning_outcome",
        "resolution_status",
    ):
        op.drop_index(f"ix_issue_outcome_episodes_{column}", table_name="issue_outcome_episodes")
        op.drop_column("issue_outcome_episodes", column)
    op.drop_index(
        "ix_issue_outcome_episodes_actor_cohort_hash",
        table_name="issue_outcome_episodes",
    )
    op.drop_column("issue_outcome_episodes", "actor_cohort_hash")
