import http from "k6/http";
import { check } from "k6";

const BASE_URL = requiredEnv("BASE_URL").replace(/\/$/, "");
const ORIGIN = requiredEnv("ORIGIN").replace(/\/$/, "");
const ADMIN_SESSION_TOKEN = requiredEnv("ADMIN_SESSION_TOKEN");
const ADMIN_COOKIE_NAME = __ENV.ADMIN_COOKIE_NAME || "support_admin_session";
const EXPECTED_MIN_ITEMS = nonNegativeInteger(
  __ENV.ADMIN_EXPECTED_MIN_ITEMS || "9800",
  "ADMIN_EXPECTED_MIN_ITEMS",
);
const DURATION = __ENV.ADMIN_TEST_DURATION || "30m";
const SUMMARY_PATH = __ENV.SUMMARY_PATH || "admin-presence-summary.json";

if ((__ENV.CONFIRM_STAGING || "") !== "1") {
  throw new Error("CONFIRM_STAGING=1 is required for the administrator Presence load test");
}

export const options = {
  scenarios: {
    adminPresence: {
      executor: "constant-arrival-rate",
      rate: 1,
      timeUnit: "15s",
      duration: DURATION,
      preAllocatedVUs: 1,
      maxVUs: 3,
    },
  },
  thresholds: {
    checks: ["rate>0.995"],
    http_req_failed: ["rate<0.005"],
    http_req_duration: ["p(95)<250", "p(99)<500"],
  },
};

export default function () {
  const response = http.get(
    BASE_URL + "/v1/admin/presence?active_within_seconds=300",
    {
      headers: {
        Accept: "application/json",
        Cookie: ADMIN_COOKIE_NAME + "=" + ADMIN_SESSION_TOKEN,
        Origin: ORIGIN,
      },
      tags: { name: "GET /v1/admin/presence" },
      timeout: "10s",
    },
  );
  let itemCount = -1;
  try {
    const payload = JSON.parse(String(response.body || "{}"));
    itemCount = Array.isArray(payload.items) ? payload.items.length : -1;
  } catch (_error) {
    itemCount = -1;
  }
  check(response, {
    "administrator Presence returns 200": (result) => result.status === 200,
    "administrator Presence returns the expected population": () =>
      itemCount >= EXPECTED_MIN_ITEMS,
  });
}

export function handleSummary(data) {
  const duration = data.metrics.http_req_duration?.values || {};
  const failed = data.metrics.http_req_failed?.values || {};
  const checks = data.metrics.checks?.values || {};
  const requests = data.metrics.http_reqs?.values || {};
  const summary = {
    schema_version: 1,
    generated_at: new Date().toISOString(),
    expected_min_items: EXPECTED_MIN_ITEMS,
    requests: Number(requests.count || 0),
    checks_rate: Number(checks.rate || 0),
    failure_rate: Number(failed.rate || 0),
    p95_ms: Number(duration["p(95)"] || 0),
    p99_ms: Number(duration["p(99)"] || 0),
  };
  const rendered = JSON.stringify(summary, null, 2) + "\n";
  return { stdout: rendered, [SUMMARY_PATH]: rendered };
}

function requiredEnv(name) {
  const value = (__ENV[name] || "").trim();
  if (!value) throw new Error(name + " is required");
  return value;
}

function nonNegativeInteger(value, name) {
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < 0) {
    throw new Error(name + " must be a non-negative integer");
  }
  return parsed;
}
