import argparse
import json
from pathlib import Path
from typing import Any


def evaluate(payload: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if payload.get("schema_version") != 1:
        failures.append("unsupported administrator Presence summary schema")
    checks_rate = _number(payload, "checks_rate")
    failure_rate = _number(payload, "failure_rate")
    p95_ms = _number(payload, "p95_ms")
    p99_ms = _number(payload, "p99_ms")
    requests = _integer(payload, "requests")
    if requests < 100:
        failures.append(f"administrator Presence test produced only {requests} requests")
    if checks_rate <= 0.995:
        failures.append(f"checks rate {checks_rate:.6f} must be greater than 0.995")
    if failure_rate >= 0.005:
        failures.append(f"failure rate {failure_rate:.6f} must be below 0.005")
    if p95_ms >= 250:
        failures.append(f"P95 {p95_ms:.2f} ms exceeds 250 ms")
    if p99_ms >= 500:
        failures.append(f"P99 {p99_ms:.2f} ms exceeds 500 ms")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Enforce the administrator Presence load gate.")
    parser.add_argument("summary", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.summary.read_text(encoding="utf-8"))
    failures = evaluate(payload)
    print(json.dumps({**payload, "status": "passed" if not failures else "failed"}, indent=2))
    for failure in failures:
        print(f"FAILED: {failure}")
    return 1 if failures else 0


def _number(payload: dict[str, Any], key: str) -> float:
    value = payload.get(key)
    if not isinstance(value, int | float) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{key} must be a non-negative number")
    return float(value)


def _integer(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{key} must be a non-negative integer")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
