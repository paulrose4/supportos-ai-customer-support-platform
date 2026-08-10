import http from "k6/http";
import { check, sleep } from "k6";

const BASE_URL = requiredEnv("BASE_URL").replace(/\/$/, "");
const ORIGIN = requiredEnv("ORIGIN").replace(/\/$/, "");
const PUBLIC_WIDGET_ID = requiredEnv("PUBLIC_WIDGET_ID");
const VISITOR_ID = "rate-limit-" + (__ENV.SHARD_ID || "0");
const PAGE_VIEW_ID = VISITOR_ID + "-page";

if ((__ENV.CONFIRM_STAGING || "") !== "1") {
  throw new Error("CONFIRM_STAGING=1 is required; rate-limit tests must target Staging");
}

export const options = {
  vus: 1,
  iterations: 1,
  thresholds: {
    checks: ["rate==1"],
  },
};

export default function () {
  const statuses = [];
  const enter = post({
    public_widget_id: PUBLIC_WIDGET_ID,
    visitor_id: VISITOR_ID,
    event: "enter",
  });
  statuses.push(enter.status);
  const payload = parseJson(enter);
  const token = typeof payload?.presence_token === "string" ? payload.presence_token : "";
  if (!token) {
    check(enter, { "rate-limit test received a Presence token": () => false });
    return;
  }

  for (let index = 0; index < 7; index += 1) {
    sleep(1);
    const heartbeat = post({
      presence_token: token,
      visitor_id: VISITOR_ID,
      event: "heartbeat",
    });
    statuses.push(heartbeat.status);
  }

  const accepted = statuses.filter((status) => status === 200).length;
  const limited = statuses.filter((status) => status === 429).length;
  check({ accepted, limited }, {
    "six or fewer requests were accepted": (result) => result.accepted <= 6,
    "the per-visitor limiter returned 429": (result) => result.limited >= 1,
  });
}

function post(credentials) {
  return http.post(
    BASE_URL + "/v1/public-widget/presence",
    JSON.stringify({
      ...credentials,
      page_path: "/presence-rate-limit",
      page_view_id: PAGE_VIEW_ID,
      widget_state: "closed",
      presence_source: "page_load",
    }),
    {
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        Origin: ORIGIN,
      },
      tags: { endpoint: "presence-rate-limit" },
      timeout: "10s",
    },
  );
}

function parseJson(response) {
  try {
    return JSON.parse(String(response.body || "{}"));
  } catch (_error) {
    return null;
  }
}

function requiredEnv(name) {
  const value = (__ENV[name] || "").trim();
  if (!value) throw new Error(name + " is required");
  return value;
}
