from migrations.versions import j1f2a3b4c5d6_repair_knowledge_version_constraints as migration


def test_repair_drops_truncated_legacy_constraint_by_column_signature(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    truncated_name = "uq_knowledge_document_versions_tenant_id_document_id_co_f9ef"
    monkeypatch.setattr(
        migration,
        "_unique_constraints",
        lambda: [
            {
                "name": truncated_name,
                "column_names": ["tenant_id", "document_id", "content_hash"],
            },
            {
                "name": "uq_knowledge_document_versions_tenant_id_version_id",
                "column_names": ["tenant_id", "version_id"],
            },
        ],
    )
    dropped: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        migration.op,
        "drop_constraint",
        lambda name, table, type_: dropped.append((name, table, type_)),
    )

    migration._drop_legacy_content_identity_constraint()

    assert dropped == [(truncated_name, "knowledge_document_versions", "unique")]


def test_repair_rejects_multiple_legacy_constraints(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        migration,
        "_unique_constraints",
        lambda: [
            {
                "name": "legacy-a",
                "column_names": ["tenant_id", "document_id", "content_hash"],
            },
            {
                "name": "legacy-b",
                "column_names": ["tenant_id", "document_id", "content_hash"],
            },
        ],
    )

    try:
        migration._drop_legacy_content_identity_constraint()
    except RuntimeError as exc:
        assert "multiple legacy content identity constraints" in str(exc)
    else:
        raise AssertionError("multiple matching constraints must fail closed")
