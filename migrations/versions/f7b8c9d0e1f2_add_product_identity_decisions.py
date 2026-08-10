"""add versioned product identities and authoritative winner decisions"""

from collections.abc import Sequence
import unicodedata

import sqlalchemy as sa
from alembic import op

revision: str = "f7b8c9d0e1f2"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NORMALIZATION_VERSION = "product-identity-v1"
_PLACEHOLDERS = frozenset({"", "0", "n/a", "na", "none", "null", "unknown", "default", "undefined", "-", "_"})
_BACKFILL_TABLES = (
    "web_crawl_manifest_items",
    "web_crawl_page_states",
    "web_sync_job_items",
    "product_fact_snapshots",
)


def _normalize(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = unicodedata.normalize("NFKC", str(value)).strip().casefold()
    if normalized in _PLACEHOLDERS:
        return None
    compact = "".join(normalized.split())
    if compact in _PLACEHOLDERS or not any(character.isalnum() for character in compact):
        return None
    if len(compact) > 500:
        raise RuntimeError("normalized product identity exceeds the persisted 500 character bound")
    return compact


def _grant_backfill_access(table: str) -> None:
    op.execute(f"DROP POLICY IF EXISTS product_identity_migration_backfill ON {table}")
    op.execute(
        f"CREATE POLICY product_identity_migration_backfill ON {table} "
        "FOR ALL TO CURRENT_USER USING (true) WITH CHECK (true)"
    )


def _revoke_backfill_access(table: str) -> None:
    op.execute(f"DROP POLICY IF EXISTS product_identity_migration_backfill ON {table}")


def _backfill(table: str) -> None:
    connection = op.get_bind()
    cursor = 0
    while True:
        rows = connection.execute(
            sa.text(
                f"SELECT id, product_key FROM {table} "
                "WHERE id > :cursor ORDER BY id LIMIT 1000"
            ),
            {"cursor": cursor},
        ).mappings().all()
        if not rows:
            return
        updates = []
        for row in rows:
            normalized = _normalize(row["product_key"])
            updates.append(
                {
                    "row_id": row["id"],
                    "normalized": normalized,
                    "version": _NORMALIZATION_VERSION if normalized is not None else None,
                }
            )
        connection.execute(
            sa.text(
                f"UPDATE {table} SET normalized_product_key=:normalized, "
                "normalization_version=:version WHERE id=:row_id"
            ),
            updates,
        )
        cursor = int(rows[-1]["id"])


def _assert_product_snapshot_identities_are_unique() -> None:
    missing_count = op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM product_fact_snapshots "
            "WHERE normalized_product_key IS NULL"
        )
    )
    if missing_count:
        raise RuntimeError(
            "cannot enforce normalized product identity; "
            f"{missing_count} product snapshot rows contain missing or placeholder identities"
        )
    collision = op.get_bind().execute(
        sa.text(
            """
            SELECT tenant_id, site_id, snapshot_id, normalized_product_key, count(*) AS item_count
            FROM product_fact_snapshots
            GROUP BY tenant_id, site_id, snapshot_id, normalized_product_key
            HAVING normalized_product_key IS NOT NULL AND count(*) > 1
            ORDER BY item_count DESC
            LIMIT 1
            """
        )
    ).mappings().first()
    if collision is not None:
        raise RuntimeError(
            "cannot enforce normalized product identity uniqueness; "
            f"snapshot {collision['tenant_id']}/{collision['site_id']}/"
            f"{collision['snapshot_id']} contains {collision['item_count']} rows for "
            f"{collision['normalized_product_key']}"
        )


def upgrade() -> None:
    if op.get_context().as_sql:
        raise RuntimeError(
            "product identity migration requires online mode because its Unicode NFKC/casefold "
            "backfill cannot be represented safely as PostgreSQL SQL"
        )

    for table in _BACKFILL_TABLES:
        op.add_column(table, sa.Column("normalized_product_key", sa.String(500), nullable=True))
        op.add_column(table, sa.Column("normalization_version", sa.String(100), nullable=True))

    for table in _BACKFILL_TABLES:
        _grant_backfill_access(table)
        try:
            _backfill(table)
            if table == "product_fact_snapshots":
                _assert_product_snapshot_identities_are_unique()
        finally:
            _revoke_backfill_access(table)

    op.alter_column("product_fact_snapshots", "normalized_product_key", nullable=False)
    op.alter_column("product_fact_snapshots", "normalization_version", nullable=False)

    op.create_index(
        "ix_web_sync_job_items_normalized_product_key",
        "web_sync_job_items",
        ["tenant_id", "job_id", "policy_version", "normalized_product_key", "ordinal"],
    )
    op.create_index(
        "ix_web_crawl_manifest_items_normalized_product_key",
        "web_crawl_manifest_items",
        ["tenant_id", "site_id", "manifest_id", "normalized_product_key"],
    )
    op.create_index(
        "ix_web_crawl_page_states_normalized_product_key",
        "web_crawl_page_states",
        ["tenant_id", "site_id", "normalized_product_key"],
    )
    op.create_index(
        "ix_product_fact_snapshots_normalized_product_key",
        "product_fact_snapshots",
        ["normalized_product_key"],
    )
    op.create_unique_constraint(
        "uq_product_fact_snapshot_normalized_identity",
        "product_fact_snapshots",
        ["tenant_id", "site_id", "snapshot_id", "normalized_product_key"],
    )

    op.create_table(
        "web_sync_product_identity_decisions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(100), nullable=False),
        sa.Column("job_id", sa.String(100), nullable=False),
        sa.Column("normalized_product_key", sa.String(500), nullable=False),
        sa.Column("winner_item_id", sa.String(100), nullable=False),
        sa.Column("winner_url", sa.Text(), nullable=False),
        sa.Column("state", sa.String(30), nullable=False, server_default="selected"),
        sa.Column("policy_version", sa.String(100), nullable=False),
        sa.Column("normalization_version", sa.String(100), nullable=False),
        sa.Column("decision_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("decision_reason", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "decision_revision >= 1 AND normalized_product_key <> '' "
            "AND state IN ('selected', 'promoted', 'unresolved')",
            name="ck_web_sync_product_identity_decision_valid",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            ["web_sync_jobs.tenant_id", "web_sync_jobs.job_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "job_id", "winner_item_id"],
            [
                "web_sync_job_items.tenant_id",
                "web_sync_job_items.job_id",
                "web_sync_job_items.item_id",
            ],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "job_id",
            "normalized_product_key",
            name="uq_web_sync_product_identity_decision",
        ),
    )
    for column in ("tenant_id", "job_id", "normalized_product_key", "winner_item_id", "state", "updated_at"):
        op.create_index(
            f"ix_web_sync_product_identity_decisions_{column}",
            "web_sync_product_identity_decisions",
            [column],
        )
    op.execute("ALTER TABLE web_sync_product_identity_decisions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE web_sync_product_identity_decisions FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON web_sync_product_identity_decisions "
        "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')) "
        "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON web_sync_product_identity_decisions")
    op.drop_table("web_sync_product_identity_decisions")
    op.drop_constraint(
        "uq_product_fact_snapshot_normalized_identity",
        "product_fact_snapshots",
        type_="unique",
    )
    op.drop_index(
        "ix_product_fact_snapshots_normalized_product_key",
        table_name="product_fact_snapshots",
    )
    op.drop_index(
        "ix_web_crawl_page_states_normalized_product_key",
        table_name="web_crawl_page_states",
    )
    op.drop_index(
        "ix_web_crawl_manifest_items_normalized_product_key",
        table_name="web_crawl_manifest_items",
    )
    op.drop_index(
        "ix_web_sync_job_items_normalized_product_key",
        table_name="web_sync_job_items",
    )
    for table in reversed(_BACKFILL_TABLES):
        op.drop_column(table, "normalization_version")
        op.drop_column(table, "normalized_product_key")
