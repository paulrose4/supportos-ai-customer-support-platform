from collections.abc import Mapping, Sequence

from sqlalchemy import inspect
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

_TABLE = "knowledge_document_versions"
_LEGACY_UNIQUE_COLUMNS = ("tenant_id", "document_id", "content_hash")
_VERSION_UNIQUE_COLUMNS = ("tenant_id", "version_id")
_LOOKUP_INDEX = "ix_knowledge_document_versions_document_content_hash"
_LIVE_SNAPSHOT_INDEX = "uq_kdv_tenant_snapshot_document_live"
_LIVE_SNAPSHOT_COLUMNS = ("tenant_id", "snapshot_id", "document_id")
_REQUIRED_COLUMNS = frozenset({"snapshot_id", "lifecycle_state"})
_LIFECYCLE_VALUES = frozenset({"draft", "staged", "indexed", "publishable", "active", "discarded"})


class KnowledgeSchemaContractError(RuntimeError):
    """The database cannot safely persist or publish knowledge versions."""


class PostgreSQLKnowledgeSchemaDependency:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def check(self) -> None:
        async with self._engine.connect() as connection:
            await connection.run_sync(validate_knowledge_schema_contract)


def _column_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(str(column) for column in value)


def _dialect_options(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def validate_knowledge_schema_contract(connection: Connection) -> None:
    inspector = inspect(connection)
    if _TABLE not in inspector.get_table_names():
        raise KnowledgeSchemaContractError(f"required table {_TABLE} is missing")

    errors: list[str] = []
    columns = {str(column.get("name") or "") for column in inspector.get_columns(_TABLE)}
    missing_columns = sorted(_REQUIRED_COLUMNS - columns)
    if missing_columns:
        errors.append(f"missing columns: {', '.join(missing_columns)}")

    unique_constraints = list(inspector.get_unique_constraints(_TABLE))
    legacy = [
        item
        for item in unique_constraints
        if _column_tuple(item.get("column_names")) == _LEGACY_UNIQUE_COLUMNS
    ]
    if legacy:
        names = sorted(str(item.get("name") or "<unnamed>") for item in legacy)
        errors.append(f"legacy content identity constraints remain: {', '.join(names)}")

    version_identity = [
        item
        for item in unique_constraints
        if _column_tuple(item.get("column_names")) == _VERSION_UNIQUE_COLUMNS
    ]
    if len(version_identity) != 1:
        errors.append("tenant/version identity constraint is missing or duplicated")

    indexes = {str(index.get("name") or ""): index for index in inspector.get_indexes(_TABLE)}
    lookup = indexes.get(_LOOKUP_INDEX)
    if lookup is None:
        errors.append(f"lookup index {_LOOKUP_INDEX} is missing")
    elif _column_tuple(lookup.get("column_names")) != _LEGACY_UNIQUE_COLUMNS or bool(
        lookup.get("unique")
    ):
        errors.append(f"lookup index {_LOOKUP_INDEX} has an unexpected definition")

    live_snapshot = indexes.get(_LIVE_SNAPSHOT_INDEX)
    if live_snapshot is None:
        errors.append(f"live snapshot index {_LIVE_SNAPSHOT_INDEX} is missing")
    else:
        predicate = str(
            _dialect_options(live_snapshot.get("dialect_options")).get("postgresql_where", "")
        )
        if (
            _column_tuple(live_snapshot.get("column_names")) != _LIVE_SNAPSHOT_COLUMNS
            or not bool(live_snapshot.get("unique"))
            or "snapshot_id" not in predicate
            or "lifecycle_state" not in predicate
            or "discarded" not in predicate
        ):
            errors.append(
                f"live snapshot index {_LIVE_SNAPSHOT_INDEX} has an unexpected definition"
            )

    lifecycle_checks = [
        str(item.get("sqltext") or "")
        for item in inspector.get_check_constraints(_TABLE)
        if "lifecycle_state" in str(item.get("sqltext") or "")
    ]
    if not lifecycle_checks or not any(
        all(value in expression for value in _LIFECYCLE_VALUES) for expression in lifecycle_checks
    ):
        errors.append("knowledge version lifecycle check constraint is missing or incomplete")

    if errors:
        raise KnowledgeSchemaContractError("; ".join(errors))
