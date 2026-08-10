import argparse
import asyncio
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


@dataclass(frozen=True, slots=True)
class PresenceCapacitySnapshot:
    captured_at: str
    tenant_id: str
    public_widget_id: str
    visitor_sessions: int
    indexed_presence: int
    active_presence: int
    index_ttl_seconds: int


def validate_snapshot(
    *,
    phase: str,
    snapshot: PresenceCapacitySnapshot,
    baseline: PresenceCapacitySnapshot | None,
    expected_active: int,
    active_tolerance: int,
    maximum_ttl_seconds: int,
) -> list[str]:
    failures: list[str] = []
    if baseline is not None:
        if snapshot.tenant_id != baseline.tenant_id:
            failures.append("tenant ID does not match the baseline")
        if snapshot.public_widget_id != baseline.public_widget_id:
            failures.append("public Widget ID does not match the baseline")
        if snapshot.visitor_sessions != baseline.visitor_sessions:
            failures.append(
                "widget_visitor_sessions changed during a page-only Presence test "
                f"({baseline.visitor_sessions} -> {snapshot.visitor_sessions})"
            )
    if phase == "steady":
        lower = max(0, expected_active - active_tolerance)
        upper = expected_active + active_tolerance
        if not lower <= snapshot.active_presence <= upper:
            failures.append(
                f"active Presence {snapshot.active_presence} is outside {lower}..{upper}"
            )
        if snapshot.indexed_presence < snapshot.active_presence:
            failures.append("the Presence index contains fewer members than the active window")
        if not 1 <= snapshot.index_ttl_seconds <= maximum_ttl_seconds:
            failures.append(
                f"Presence index TTL {snapshot.index_ttl_seconds} is outside 1.."
                f"{maximum_ttl_seconds}"
            )
    elif phase == "stopped":
        if snapshot.active_presence != 0:
            failures.append(
                f"expected zero active visitors after the offline window, found "
                f"{snapshot.active_presence}"
            )
        if snapshot.index_ttl_seconds > maximum_ttl_seconds:
            failures.append(
                f"Presence index TTL {snapshot.index_ttl_seconds} exceeds {maximum_ttl_seconds}"
            )
    return failures


async def capture_snapshot(
    *,
    database_url: str,
    redis_url: str,
    tenant_id: str,
    public_widget_id: str,
    active_within_seconds: int,
) -> PresenceCapacitySnapshot:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    redis = Redis.from_url(redis_url, decode_responses=True)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text(
                    "SELECT count(*) FROM widget_visitor_sessions "
                    "WHERE public_widget_id = :public_widget_id"
                ),
                {"public_widget_id": public_widget_id},
            )
            visitor_sessions = int(result.scalar_one())
        index_key = f"presence:index:{tenant_id}"
        now = datetime.now(UTC)
        indexed_presence, active_presence, index_ttl = await asyncio.gather(
            redis.zcard(index_key),
            redis.zcount(index_key, now.timestamp() - active_within_seconds, "+inf"),
            redis.ttl(index_key),
        )
    finally:
        await redis.aclose()
        await engine.dispose()
    return PresenceCapacitySnapshot(
        captured_at=datetime.now(UTC).isoformat(),
        tenant_id=tenant_id,
        public_widget_id=public_widget_id,
        visitor_sessions=int(visitor_sessions),
        indexed_presence=int(indexed_presence),
        active_presence=int(active_presence),
        index_ttl_seconds=int(index_ttl),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only PostgreSQL and Redis audit for a staging Presence capacity run."
    )
    parser.add_argument("--phase", choices=("baseline", "steady", "stopped"), required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--public-widget-id", required=True)
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument("--expected-active", type=int, default=10_000)
    parser.add_argument("--active-tolerance", type=int, default=200)
    parser.add_argument("--active-within-seconds", type=int, default=60)
    parser.add_argument("--maximum-ttl-seconds", type=int, default=360)
    parser.add_argument(
        "--confirm-staging",
        action="store_true",
        help="Required acknowledgement that the configured services are isolated staging services.",
    )
    return parser


async def _run() -> int:
    args = _parser().parse_args()
    if not args.confirm_staging:
        raise ValueError("--confirm-staging is required")
    if not _IDENTIFIER_PATTERN.fullmatch(args.tenant_id):
        raise ValueError("tenant ID contains unsupported characters")
    if not _IDENTIFIER_PATTERN.fullmatch(args.public_widget_id):
        raise ValueError("public Widget ID contains unsupported characters")
    if not 15 <= args.active_within_seconds <= 300:
        raise ValueError("active window must be between 15 and 300 seconds")
    if args.expected_active < 0 or args.active_tolerance < 0:
        raise ValueError("expected active count and tolerance must be non-negative")
    database_url = _required_env("STAGING_AUDIT_DATABASE_URL")
    redis_url = _required_env("STAGING_REDIS_URL")
    snapshot = await capture_snapshot(
        database_url=database_url,
        redis_url=redis_url,
        tenant_id=args.tenant_id,
        public_widget_id=args.public_widget_id,
        active_within_seconds=args.active_within_seconds,
    )
    if args.phase == "baseline":
        _atomic_write(args.state_file, asdict(snapshot))
        print(json.dumps(asdict(snapshot), indent=2, sort_keys=True))
        return 0
    baseline = _load_snapshot(args.state_file)
    failures = validate_snapshot(
        phase=args.phase,
        snapshot=snapshot,
        baseline=baseline,
        expected_active=args.expected_active,
        active_tolerance=args.active_tolerance,
        maximum_ttl_seconds=args.maximum_ttl_seconds,
    )
    output = {
        **asdict(snapshot),
        "phase": args.phase,
        "status": "passed" if not failures else "failed",
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    for failure in failures:
        print(f"FAILED: {failure}")
    return 1 if failures else 0


def _load_snapshot(path: Path) -> PresenceCapacitySnapshot:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return PresenceCapacitySnapshot(**payload)


def _atomic_write(path: Path, payload: dict[str, int | str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
