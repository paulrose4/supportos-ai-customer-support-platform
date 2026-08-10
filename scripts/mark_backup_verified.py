import argparse
import json
from datetime import UTC, datetime
from pathlib import Path


def mark_verified(
    *,
    status_directory: Path,
    artifact_type: str,
    approval_reference: str,
    actor_subject_id: str,
    verified_at: datetime,
) -> dict[str, object]:
    if artifact_type not in {"postgres", "qdrant"}:
        raise ValueError("artifact_type must be postgres or qdrant")
    if not approval_reference.strip() or not actor_subject_id.strip():
        raise ValueError("approval reference and trusted actor are required")
    path = status_directory / f"{artifact_type}.json"
    if not path.is_file():
        raise FileNotFoundError(f"backup status does not exist: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("artifact_type") != artifact_type:
        raise ValueError("backup status artifact type does not match")
    if (
        payload.get("restore_verification_reference") == approval_reference.strip()
        and payload.get("restore_verified_by") == actor_subject_id.strip()
        and payload.get("restore_verified_at")
    ):
        return payload
    payload["restore_verified_at"] = verified_at.astimezone(UTC).isoformat()
    payload["restore_verification_reference"] = approval_reference.strip()
    payload["restore_verified_by"] = actor_subject_id.strip()
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Mark a completed backup restore drill as operator-verified."
    )
    parser.add_argument("--status-directory", type=Path, default=Path("./backups/status"))
    parser.add_argument("--artifact-type", required=True, choices=("postgres", "qdrant"))
    parser.add_argument("--approval-reference", required=True)
    parser.add_argument("--actor-subject-id", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        payload = mark_verified(
            status_directory=args.status_directory,
            artifact_type=args.artifact_type,
            approval_reference=args.approval_reference,
            actor_subject_id=args.actor_subject_id,
            verified_at=datetime.now(UTC),
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(
        json.dumps(
            {
                "status": "verified",
                "artifact_type": payload["artifact_type"],
                "file_name": payload["file_name"],
                "restore_verified_at": payload["restore_verified_at"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
