import json

from scripts.audit_presence_staging import PresenceCapacitySnapshot, validate_snapshot
from scripts.check_admin_presence_result import evaluate as evaluate_admin_presence
from scripts.check_presence_load_results import (
    PresenceLoadGate,
    evaluate_summaries,
    parse_summary,
)


def summary_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "shard_id": "0",
        "profile": "capacity",
        "configured_vus": 2500,
        "requests": 100_000,
        "checks_rate": 1.0,
        "failure_rate": 0.0,
        "p95_ms": 100.0,
        "p99_ms": 200.0,
        "rate_limited": 0,
        "unauthorized": 0,
        "server_errors": 0,
        "unexpected_statuses": 0,
    }
    payload.update(overrides)
    return payload


def test_distributed_presence_load_gate_passes_four_healthy_shards() -> None:
    summaries = [parse_summary(summary_payload(shard_id=str(index))) for index in range(4)]

    aggregate, failures = evaluate_summaries(
        summaries,
        PresenceLoadGate(expected_shards=4, expected_vus=10_000, minimum_requests=300_000),
    )

    assert failures == []
    assert aggregate["status"] == "passed"
    assert aggregate["requests"] == 400_000


def test_presence_load_gate_fails_on_latency_rate_limit_and_duplicate_shard() -> None:
    summaries = [
        parse_summary(summary_payload(shard_id="0")),
        parse_summary(
            summary_payload(
                shard_id="0",
                p95_ms=300,
                failure_rate=0.01,
                rate_limited=2,
            )
        ),
    ]

    aggregate, failures = evaluate_summaries(
        summaries,
        PresenceLoadGate(expected_shards=2, expected_vus=5000, minimum_requests=100),
    )

    assert aggregate["status"] == "failed"
    assert any("unique shards" in item for item in failures)
    assert any("P95" in item for item in failures)
    assert any("rate-limited" in item for item in failures)


def test_staging_audit_rejects_session_growth_and_wrong_online_count() -> None:
    baseline = PresenceCapacitySnapshot(
        captured_at="2026-01-01T00:00:00+00:00",
        tenant_id="tenant-staging",
        public_widget_id="site_pub_staging",
        visitor_sessions=5,
        indexed_presence=0,
        active_presence=0,
        index_ttl_seconds=-2,
    )
    steady = PresenceCapacitySnapshot(
        captured_at="2026-01-01T00:30:00+00:00",
        tenant_id="tenant-staging",
        public_widget_id="site_pub_staging",
        visitor_sessions=6,
        indexed_presence=9000,
        active_presence=9000,
        index_ttl_seconds=360,
    )

    failures = validate_snapshot(
        phase="steady",
        snapshot=steady,
        baseline=baseline,
        expected_active=10_000,
        active_tolerance=200,
        maximum_ttl_seconds=360,
    )

    assert any("widget_visitor_sessions changed" in item for item in failures)
    assert any("outside" in item for item in failures)


def test_summary_payload_is_json_serializable() -> None:
    assert json.loads(json.dumps(summary_payload()))["schema_version"] == 1


def test_admin_presence_gate_checks_large_response_latency() -> None:
    assert (
        evaluate_admin_presence(
            {
                "schema_version": 1,
                "requests": 120,
                "checks_rate": 1.0,
                "failure_rate": 0.0,
                "p95_ms": 100,
                "p99_ms": 200,
            }
        )
        == []
    )

    failures = evaluate_admin_presence(
        {
            "schema_version": 1,
            "requests": 120,
            "checks_rate": 1.0,
            "failure_rate": 0.0,
            "p95_ms": 300,
            "p99_ms": 600,
        }
    )
    assert any("P95" in item for item in failures)
    assert any("P99" in item for item in failures)
