import json
from datetime import UTC, datetime

from scripts.mark_backup_verified import mark_verified


def test_mark_backup_verified_is_atomic_and_idempotent(tmp_path) -> None:
    path = tmp_path / "postgres.json"
    path.write_text(
        json.dumps(
            {
                "artifact_type": "postgres",
                "completed_at": datetime.now(UTC).isoformat(),
                "file_name": "postgres.dump",
                "size_bytes": 10,
                "sha256": "a" * 64,
                "restore_verified_at": None,
            }
        ),
        encoding="utf-8",
    )
    verified_at = datetime.now(UTC)

    first = mark_verified(
        status_directory=tmp_path,
        artifact_type="postgres",
        approval_reference="RESTORE-42",
        actor_subject_id="operator-1",
        verified_at=verified_at,
    )
    second = mark_verified(
        status_directory=tmp_path,
        artifact_type="postgres",
        approval_reference="RESTORE-42",
        actor_subject_id="operator-1",
        verified_at=datetime.now(UTC),
    )

    assert first["restore_verified_at"] == verified_at.isoformat()
    assert second["restore_verified_at"] == first["restore_verified_at"]
    assert not (tmp_path / "postgres.json.tmp").exists()
