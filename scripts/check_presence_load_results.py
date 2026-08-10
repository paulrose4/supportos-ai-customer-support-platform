import argparse
import glob
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class PresenceLoadSummary:
    shard_id: str
    profile: str
    configured_vus: int
    requests: int
    checks_rate: float
    failure_rate: float
    p95_ms: float
    p99_ms: float
    rate_limited: int
    unauthorized: int
    server_errors: int
    unexpected_statuses: int


@dataclass(frozen=True, slots=True)
class PresenceLoadGate:
    expected_shards: int
    expected_vus: int
    minimum_requests: int
    minimum_checks_rate: float = 0.995
    maximum_failure_rate: float = 0.005
    maximum_p95_ms: float = 250.0
    maximum_p99_ms: float = 500.0


def parse_summary(payload: dict[str, Any]) -> PresenceLoadSummary:
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported presence load summary schema")
    return PresenceLoadSummary(
        shard_id=_string(payload, "shard_id"),
        profile=_string(payload, "profile"),
        configured_vus=_non_negative_int(payload, "configured_vus"),
        requests=_non_negative_int(payload, "requests"),
        checks_rate=_rate(payload, "checks_rate"),
        failure_rate=_rate(payload, "failure_rate"),
        p95_ms=_non_negative_float(payload, "p95_ms"),
        p99_ms=_non_negative_float(payload, "p99_ms"),
        rate_limited=_non_negative_int(payload, "rate_limited"),
        unauthorized=_non_negative_int(payload, "unauthorized"),
        server_errors=_non_negative_int(payload, "server_errors"),
        unexpected_statuses=_non_negative_int(payload, "unexpected_statuses"),
    )


def evaluate_summaries(
    summaries: list[PresenceLoadSummary],
    gate: PresenceLoadGate,
) -> tuple[dict[str, int | float | str], list[str]]:
    failures: list[str] = []
    shard_ids = {item.shard_id for item in summaries}
    profiles = {item.profile for item in summaries}
    total_requests = sum(item.requests for item in summaries)
    total_vus = sum(item.configured_vus for item in summaries)
    checks_rate = _weighted_rate(summaries, "checks_rate")
    failure_rate = _weighted_rate(summaries, "failure_rate")
    worst_p95 = max((item.p95_ms for item in summaries), default=0.0)
    worst_p99 = max((item.p99_ms for item in summaries), default=0.0)
    rate_limited = sum(item.rate_limited for item in summaries)
    unauthorized = sum(item.unauthorized for item in summaries)
    server_errors = sum(item.server_errors for item in summaries)
    unexpected = sum(item.unexpected_statuses for item in summaries)

    if len(summaries) != gate.expected_shards or len(shard_ids) != gate.expected_shards:
        failures.append(f"expected {gate.expected_shards} unique shards, received {len(shard_ids)}")
    if len(profiles) != 1:
        failures.append("all shard summaries must use the same profile")
    if total_vus != gate.expected_vus:
        failures.append(f"expected {gate.expected_vus} configured VUs, received {total_vus}")
    if total_requests < gate.minimum_requests:
        failures.append(
            f"expected at least {gate.minimum_requests} requests, received {total_requests}"
        )
    if checks_rate <= gate.minimum_checks_rate:
        failures.append(
            f"checks rate {checks_rate:.6f} must be greater than {gate.minimum_checks_rate:.6f}"
        )
    if failure_rate >= gate.maximum_failure_rate:
        failures.append(
            f"failure rate {failure_rate:.6f} must be below {gate.maximum_failure_rate:.6f}"
        )
    if worst_p95 >= gate.maximum_p95_ms:
        failures.append(f"worst shard P95 {worst_p95:.2f} ms exceeds the release target")
    if worst_p99 >= gate.maximum_p99_ms:
        failures.append(f"worst shard P99 {worst_p99:.2f} ms exceeds the release target")
    if rate_limited:
        failures.append(f"received {rate_limited} unexpected rate-limited responses")
    if unauthorized:
        failures.append(f"received {unauthorized} unauthorized responses")
    if server_errors:
        failures.append(f"received {server_errors} server errors")
    if unexpected:
        failures.append(f"received {unexpected} other unexpected responses")

    aggregate: dict[str, int | float | str] = {
        "profile": next(iter(profiles), "unknown"),
        "shards": len(shard_ids),
        "configured_vus": total_vus,
        "requests": total_requests,
        "checks_rate": round(checks_rate, 8),
        "failure_rate": round(failure_rate, 8),
        "worst_shard_p95_ms": round(worst_p95, 3),
        "worst_shard_p99_ms": round(worst_p99, 3),
        "rate_limited": rate_limited,
        "unauthorized": unauthorized,
        "server_errors": server_errors,
        "unexpected_statuses": unexpected,
        "status": "passed" if not failures else "failed",
    }
    return aggregate, failures


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Aggregate distributed k6 Presence summaries and enforce release thresholds."
    )
    parser.add_argument("summaries", nargs="+", help="Summary files or glob patterns")
    parser.add_argument("--expected-shards", type=int, default=4)
    parser.add_argument("--expected-vus", type=int, default=10_000)
    parser.add_argument("--minimum-requests", type=int, default=100_000)
    return parser


def main() -> int:
    args = _parser().parse_args()
    paths = _expand_paths(args.summaries)
    summaries = [parse_summary(json.loads(path.read_text(encoding="utf-8"))) for path in paths]
    aggregate, failures = evaluate_summaries(
        summaries,
        PresenceLoadGate(
            expected_shards=args.expected_shards,
            expected_vus=args.expected_vus,
            minimum_requests=args.minimum_requests,
        ),
    )
    print(json.dumps(aggregate, indent=2, sort_keys=True))
    for failure in failures:
        print(f"FAILED: {failure}")
    return 1 if failures else 0


def _expand_paths(patterns: list[str]) -> list[Path]:
    paths: list[Path] = []
    for pattern in patterns:
        matches = [Path(value) for value in glob.glob(pattern)]
        if not matches:
            candidate = Path(pattern)
            if candidate.is_file():
                matches = [candidate]
        paths.extend(matches)
    unique = sorted({path.resolve() for path in paths})
    if not unique:
        raise ValueError("no Presence load summary files were found")
    return unique


def _weighted_rate(summaries: list[PresenceLoadSummary], field: str) -> float:
    total_requests = sum(item.requests for item in summaries)
    if total_requests == 0:
        return 0.0
    return sum(getattr(item, field) * item.requests for item in summaries) / total_requests


def _string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _non_negative_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{key} must be a non-negative integer")
    return value


def _non_negative_float(payload: dict[str, Any], key: str) -> float:
    value = payload.get(key)
    if not isinstance(value, int | float) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{key} must be a non-negative number")
    return float(value)


def _rate(payload: dict[str, Any], key: str) -> float:
    value = _non_negative_float(payload, key)
    if value > 1:
        raise ValueError(f"{key} must be between zero and one")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
