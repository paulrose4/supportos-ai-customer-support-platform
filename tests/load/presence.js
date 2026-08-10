import http from "k6/http";
import { check, sleep } from "k6";
import { Counter } from "k6/metrics";

const BASE_URL = requiredEnv("BASE_URL").replace(/\/$/, "");
const ORIGIN = requiredEnv("ORIGIN").replace(/\/$/, "");
const PUBLIC_WIDGET_ID = requiredEnv("PUBLIC_WIDGET_ID");
const SHARD_ID = safeIdentifier(__ENV.SHARD_ID || "0", "SHARD_ID");
const PROFILE = __ENV.PROFILE || "smoke";
const CONFIRM_STAGING = __ENV.CONFIRM_STAGING || "";
const VUS = positiveInteger(__ENV.VUS || "10", "VUS");
const HEARTBEAT_MIN_SECONDS = positiveNumber(
  __ENV.HEARTBEAT_MIN_SECONDS || "20",
  "HEARTBEAT_MIN_SECONDS",
);
const HEARTBEAT_MAX_SECONDS = positiveNumber(
  __ENV.HEARTBEAT_MAX_SECONDS || "25",
  "HEARTBEAT_MAX_SECONDS",
);
const TOKEN_RENEWAL_SKEW_MS = 30_000;
const SUMMARY_PATH =
  __ENV.SUMMARY_PATH || "presence-summary-shard-" + SHARD_ID + ".json";

if (HEARTBEAT_MAX_SECONDS < HEARTBEAT_MIN_SECONDS) {
  throw new Error("HEARTBEAT_MAX_SECONDS must be greater than or equal to HEARTBEAT_MIN_SECONDS");
}

const profiles = {
  smoke: [
    { duration: "10s", target: VUS },
    { duration: "30s", target: VUS },
    { duration: "10s", target: 0 },
  ],
  capacity: [
    { duration: "10m", target: VUS },
    { duration: "30m", target: VUS },
    { duration: "2m", target: 0 },
  ],
  peak: [
    { duration: "10m", target: VUS },
    { duration: "2h", target: VUS },
    { duration: "2m", target: 0 },
  ],
  soak: [
    { duration: "10m", target: VUS },
    { duration: "24h", target: VUS },
    { duration: "2m", target: 0 },
  ],
};

if (!Object.hasOwn(profiles, PROFILE)) {
  throw new Error("PROFILE must be one of: " + Object.keys(profiles).join(", "));
}
if (CONFIRM_STAGING !== "1") {
  throw new Error("CONFIRM_STAGING=1 is required; the load target must be isolated Staging");
}

const unexpectedStatuses = new Counter("presence_unexpected_statuses");
const rateLimitedResponses = new Counter("presence_rate_limited_responses");
const unauthorizedResponses = new Counter("presence_unauthorized_responses");
const serverErrorResponses = new Counter("presence_server_error_responses");
const tokenRenewals = new Counter("presence_token_renewals");

export const options = {
  scenarios: {
    presence: {
      executor: "ramping-vus",
      startVUs: 0,
      stages: profiles[PROFILE],
      gracefulRampDown: "30s",
      gracefulStop: "30s",
    },
  },
  thresholds: {
    checks: ["rate>0.995"],
    "http_req_failed{endpoint:presence}": ["rate<0.005"],
    "http_req_duration{endpoint:presence}": ["p(95)<250", "p(99)<500"],
    presence_rate_limited_responses: ["count==0"],
    presence_server_error_responses: ["count==0"],
    presence_unexpected_statuses: ["count==0"],
  },
  userAgent:
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) " +
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36",
};

let visitorId;
let pageViewId;
let presenceToken = null;
let presenceTokenExpiresAt = 0;

export default function () {
  initializeVisitor();
  if (!presenceToken || Date.now() + TOKEN_RENEWAL_SKEW_MS >= presenceTokenExpiresAt) {
    registerPresence();
  } else {
    heartbeat();
  }
  sleep(randomBetween(HEARTBEAT_MIN_SECONDS, HEARTBEAT_MAX_SECONDS));
}

function initializeVisitor() {
  if (visitorId) return;
  visitorId = "load-" + SHARD_ID + "-" + __VU;
  pageViewId = visitorId + "-page";
}

function registerPresence() {
  tokenRenewals.add(1);
  const response = postPresence({
    public_widget_id: PUBLIC_WIDGET_ID,
    visitor_id: visitorId,
    event: "enter",
    page_path: "/presence-load/" + SHARD_ID + "/" + __VU,
    page_title: "Presence capacity test",
    page_view_id: pageViewId,
    widget_state: "closed",
    presence_source: "page_load",
    language: "en-US",
    timezone: "UTC",
  });
  if (!recordResponse(response)) return;
  const payload = responseJson(response);
  const token = typeof payload?.presence_token === "string" ? payload.presence_token : "";
  const expiresAt = Date.parse(String(payload?.presence_token_expires_at || ""));
  if (!token || !Number.isFinite(expiresAt)) {
    unexpectedStatuses.add(1);
    presenceToken = null;
    presenceTokenExpiresAt = 0;
    return;
  }
  presenceToken = token;
  presenceTokenExpiresAt = expiresAt;
}

function heartbeat() {
  const response = postPresence({
    presence_token: presenceToken,
    visitor_id: visitorId,
    event: "heartbeat",
    page_path: "/presence-load/" + SHARD_ID + "/" + __VU,
    page_title: "Presence capacity test",
    page_view_id: pageViewId,
    widget_state: "closed",
    presence_source: "page_load",
    language: "en-US",
    timezone: "UTC",
  });
  const succeeded = recordResponse(response);
  if (response.status === 401) {
    presenceToken = null;
    presenceTokenExpiresAt = 0;
    return;
  }
  if (!succeeded) return;
  const payload = responseJson(response);
  const expiresAt = Date.parse(String(payload?.presence_token_expires_at || ""));
  if (Number.isFinite(expiresAt)) presenceTokenExpiresAt = expiresAt;
}

function postPresence(payload) {
  return http.post(BASE_URL + "/v1/public-widget/presence", JSON.stringify(payload), {
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      Origin: ORIGIN,
    },
    tags: { endpoint: "presence", name: "POST /v1/public-widget/presence" },
    timeout: "10s",
  });
}

function recordResponse(response) {
  const succeeded = check(response, {
    "presence returns 200": (result) => result.status === 200,
  });
  if (response.status === 429) rateLimitedResponses.add(1);
  if (response.status === 401) unauthorizedResponses.add(1);
  if (response.status >= 500) serverErrorResponses.add(1);
  if (response.status !== 200 && response.status !== 401 && response.status !== 429) {
    unexpectedStatuses.add(1);
  }
  return succeeded;
}

function responseJson(response) {
  try {
    return JSON.parse(String(response.body || "{}"));
  } catch (_error) {
    return null;
  }
}

export function handleSummary(data) {
  const duration = metricValues(data, "http_req_duration{endpoint:presence}");
  const failed = metricValues(data, "http_req_failed{endpoint:presence}");
  const summary = {
    schema_version: 1,
    generated_at: new Date().toISOString(),
    profile: PROFILE,
    shard_id: SHARD_ID,
    configured_vus: VUS,
    heartbeat_seconds: [HEARTBEAT_MIN_SECONDS, HEARTBEAT_MAX_SECONDS],
    requests: metricValue(data, "http_reqs", "count"),
    checks_rate: metricValue(data, "checks", "rate"),
    failure_rate: failed.rate || 0,
    p95_ms: duration["p(95)"] || 0,
    p99_ms: duration["p(99)"] || 0,
    rate_limited: metricValue(data, "presence_rate_limited_responses", "count"),
    unauthorized: metricValue(data, "presence_unauthorized_responses", "count"),
    server_errors: metricValue(data, "presence_server_error_responses", "count"),
    unexpected_statuses: metricValue(data, "presence_unexpected_statuses", "count"),
    token_renewals: metricValue(data, "presence_token_renewals", "count"),
  };
  const rendered = JSON.stringify(summary, null, 2) + "\n";
  return { stdout: rendered, [SUMMARY_PATH]: rendered };
}

function metricValue(data, name, field) {
  return Number(metricValues(data, name)[field] || 0);
}

function metricValues(data, name) {
  return data.metrics[name]?.values || {};
}

function requiredEnv(name) {
  const value = (__ENV[name] || "").trim();
  if (!value) throw new Error(name + " is required");
  return value;
}

function safeIdentifier(value, name) {
  if (!/^[A-Za-z0-9_-]{1,32}$/.test(value)) {
    throw new Error(name + " must contain only letters, numbers, underscores, or hyphens");
  }
  return value;
}

function positiveInteger(value, name) {
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < 1) {
    throw new Error(name + " must be a positive integer");
  }
  return parsed;
}

function positiveNumber(value, name) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) throw new Error(name + " must be positive");
  return parsed;
}

function randomBetween(minimum, maximum) {
  return minimum + Math.random() * (maximum - minimum);
}
