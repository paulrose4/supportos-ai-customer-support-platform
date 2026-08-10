"""repair knowledge version uniqueness and assert the resulting schema

Revision ID: j1f2a3b4c5d6
Revises: i0e1f2a3b4c5
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "j1f2a3b4c5d6"
down_revision: str | None = "i0e1f2a3b4c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "knowledge_document_versions"
_LEGACY_UNIQUE_COLUMNS = ("tenant_id", "document_id", "content_hash")
_VERSION_UNIQUE_COLUMNS = ("tenant_id", "version_id")
_LOOKUP_INDEX = "ix_knowledge_document_versions_document_content_hash"
_LIVE_SNAPSHOT_INDEX = "uq_kdv_tenant_snapshot_document_live"
_LIVE_SNAPSHOT_COLUMNS = ("tenant_id", "snapshot_id", "document_id")


def _column_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(column) for column in value)


def _unique_constraints() -> list[dict[str, object]]:
    return list(sa.inspect(op.get_bind()).get_unique_constraints(_TABLE))


def _indexes() -> list[dict[str, object]]:
    return list(sa.inspect(op.get_bind()).get_indexes(_TABLE))


def _matching_unique_constraints(columns: tuple[str, ...]) -> list[dict[str, object]]:
    return [
        constraint
        for constraint in _unique_constraints()
        if _column_tuple(constraint.get("column_names")) == columns
    ]


def _matching_indexes(name: str) -> list[dict[str, object]]:
    return [index for index in _indexes() if str(index.get("name") or "") == name]


def _assert_no_live_snapshot_duplicates() -> None:
    duplicate = op.get_bind().execute(
        sa.text(
            """
            SELECT 1
            FROM knowledge_document_versions
            WHERE snapshot_id IS NOT NULL
              AND lifecycle_state <> 'discarded'
            GROUP BY tenant_id, snapshot_id, document_id
            HAVING count(*) > 1
            LIMIT 1
            """
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise RuntimeError(
            "knowledge version schema repair found multiple live versions for one snapshot "
            "document; reconcile the rows before retrying the migration"
        )


def _drop_legacy_content_identity_constraint() -> None:
    matches = _matching_unique_constraints(_LEGACY_UNIQUE_COLUMNS)
    if len(matches) > 1:
        names = sorted(str(item.get("name") or "<unnamed>") for item in matches)
        raise RuntimeError(
            "knowledge version schema repair found multiple legacy content identity "
            f"constraints: {names}"
        )
    if not matches:
        return
    name = str(matches[0].get("name") or "")
    if not name:
        raise RuntimeError("knowledge version legacy unique constraint has no database name")
    op.drop_constraint(name, _TABLE, type_="unique")


def _ensure_lookup_index() -> None:
    matches = _matching_indexes(_LOOKUP_INDEX)
    if len(matches) > 1:
        raise RuntimeError(f"knowledge version lookup index {_LOOKUP_INDEX} is duplicated")
    if matches:
        index = matches[0]
        if _column_tuple(index.get("column_names")) != _LEGACY_UNIQUE_COLUMNS or bool(
            index.get("unique")
        ):
            raise RuntimeError(
                f"knowledge version lookup index {_LOOKUP_INDEX} has an unexpected definition"
            )
        return
    op.create_index(
        _LOOKUP_INDEX,
        _TABLE,
        list(_LEGACY_UNIQUE_COLUMNS),
        unique=False,
    )


def _ensure_live_snapshot_index() -> None:
    matches = _matching_indexes(_LIVE_SNAPSHOT_INDEX)
    if len(matches) > 1:
        raise RuntimeError(f"knowledge version live snapshot index {_LIVE_SNAPSHOT_INDEX} is duplicated")
    if matches:
        index = matches[0]
        predicate = str((index.get("dialect_options") or {}).get("postgresql_where") or "")
        if (
            _column_tuple(index.get("column_names")) != _LIVE_SNAPSHOT_COLUMNS
            or not bool(index.get("unique"))
            or "snapshot_id" not in predicate
            or "lifecycle_state" not in predicate
            or "discarded" not in predicate
        ):
            raise RuntimeError(
                f"knowledge version live snapshot index {_LIVE_SNAPSHOT_INDEX} has an "
                "unexpected definition"
            )
        return
    _assert_no_live_snapshot_duplicates()
    op.create_index(
        _LIVE_SNAPSHOT_INDEX,
        _TABLE,
        list(_LIVE_SNAPSHOT_COLUMNS),
        unique=True,
        postgresql_where=sa.text(
            "snapshot_id IS NOT NULL AND lifecycle_state <> 'discarded'"
        ),
    )


def _assert_schema_contract() -> None:
    if _matching_unique_constraints(_LEGACY_UNIQUE_COLUMNS):
        raise RuntimeError("legacy knowledge content identity unique constraint is still present")
    if len(_matching_unique_constraints(_VERSION_UNIQUE_COLUMNS)) != 1:
        raise RuntimeError("knowledge version identity unique constraint is missing or duplicated")
    _ensure_lookup_index()
    _ensure_live_snapshot_index()


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.execute("SET LOCAL statement_timeout = '120s'")
    _drop_legacy_content_identity_constraint()
    _ensure_lookup_index()
    _ensure_live_snapshot_index()
    _assert_schema_contract()


def downgrade() -> None:
    matches = _matching_indexes(_LIVE_SNAPSHOT_INDEX)
    if matches:
        op.drop_index(_LIVE_SNAPSHOT_INDEX, table_name=_TABLE)
    # The removed content-identity constraint encoded an invalid invariant and
    # is intentionally not restored. Downgrading the application remains
    # compatible with the non-unique lookup index introduced by i0e1f2a3b4c5.
