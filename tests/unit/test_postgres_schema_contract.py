import pytest

from app.integrations.postgres import schema_contract


class _Inspector:
    def __init__(self, *, include_legacy: bool = False) -> None:
        self._include_legacy = include_legacy

    def get_table_names(self) -> list[str]:
        return ["knowledge_document_versions"]

    def get_columns(self, table: str) -> list[dict[str, object]]:
        assert table == "knowledge_document_versions"
        return [{"name": "snapshot_id"}, {"name": "lifecycle_state"}]

    def get_unique_constraints(self, table: str) -> list[dict[str, object]]:
        assert table == "knowledge_document_versions"
        constraints: list[dict[str, object]] = [
            {
                "name": "uq_kdv_tenant_version",
                "column_names": ["tenant_id", "version_id"],
            }
        ]
        if self._include_legacy:
            constraints.append(
                {
                    "name": "uq_knowledge_document_versions_tenant_id_document_id_co_f9ef",
                    "column_names": ["tenant_id", "document_id", "content_hash"],
                }
            )
        return constraints

    def get_indexes(self, table: str) -> list[dict[str, object]]:
        assert table == "knowledge_document_versions"
        return [
            {
                "name": "ix_knowledge_document_versions_document_content_hash",
                "column_names": ["tenant_id", "document_id", "content_hash"],
                "unique": False,
            },
            {
                "name": "uq_kdv_tenant_snapshot_document_live",
                "column_names": ["tenant_id", "snapshot_id", "document_id"],
                "unique": True,
                "dialect_options": {
                    "postgresql_where": (
                        "snapshot_id IS NOT NULL AND lifecycle_state <> 'discarded'"
                    )
                },
            },
        ]

    def get_check_constraints(self, table: str) -> list[dict[str, object]]:
        assert table == "knowledge_document_versions"
        return [
            {
                "sqltext": (
                    "lifecycle_state IN ('draft', 'staged', 'indexed', 'publishable', "
                    "'active', 'discarded')"
                )
            }
        ]


def test_schema_contract_accepts_expected_structure(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(schema_contract, "inspect", lambda connection: _Inspector())

    schema_contract.validate_knowledge_schema_contract(object())  # type: ignore[arg-type]


def test_schema_contract_rejects_truncated_legacy_constraint(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        schema_contract,
        "inspect",
        lambda connection: _Inspector(include_legacy=True),
    )

    with pytest.raises(
        schema_contract.KnowledgeSchemaContractError,
        match="legacy content identity constraints remain",
    ):
        schema_contract.validate_knowledge_schema_contract(object())  # type: ignore[arg-type]
