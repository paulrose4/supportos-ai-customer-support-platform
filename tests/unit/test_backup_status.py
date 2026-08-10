import json
from datetime import UTC, datetime

import pytest

from app.integrations.filesystem import FileBackupStatusAdapter


@pytest.mark.asyncio
async def test_file_backup_status_reads_only_expected_valid_manifests(tmp_path) -> None:
    completed_at = datetime.now(UTC)
    (tmp_path / "postgres.json").write_text(
        json.dumps(
            {
                "artifact_type": "postgres",
                "completed_at": completed_at.isoformat(),
                "file_name": "../postgres.dump",
                "size_bytes": 2048,
                "sha256": "a" * 64,
                "restore_verified_at": None,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "qdrant.json").write_text("not-json", encoding="utf-8")
    (tmp_path / "ignored.json").write_text("{}", encoding="utf-8")

    items = await FileBackupStatusAdapter(tmp_path).list_backup_statuses()

    assert len(items) == 1
    assert items[0].artifact_type == "postgres"
    assert items[0].file_name == "postgres.dump"
    assert items[0].size_bytes == 2048
