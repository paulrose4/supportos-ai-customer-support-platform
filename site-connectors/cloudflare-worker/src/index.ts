interface RateLimiter {
  limit(options: { key: string }): Promise<{ success: boolean }>;
}

interface Env {
  AGENT_API_BASE_URL: string;
  AGENT_SITE_KEY: string;
  PUBLIC_ORIGIN: string;
  ALLOW_INSECURE_AGENT_API?: string;
  ALLOW_MISSING_ORIGIN?: string;
  CHAT_LIMITER: RateLimiter;
  PRESENCE_LIMITER: RateLimiter;
  PRESENCE_SOURCE_LIMITER?: RateLimiter;
  PRESENCE_SITE_LIMITER?: RateLimiter;
}

type Operation = "chat" | "presence" | "messages";

const MAX_BODY_BYTES = 16_384;
const MAX_MESSAGE_LENGTH = 10_000;
const MAX_PAGE_PATH_LENGTH = 500;
const OPAQUE_ID_PATTERN = /^[A-Za-z0-9._:-]{1,100}$/;

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const operation = operationForPath(url.pathname);
    if (!operation) {
      return jsonResponse(404, { error: { code: "not_found", message: "Not found." } });
    }

    const originCheck = validateOrigin(request, env);
    if (!originCheck.allowed) {
      return jsonResponse(403, { error: { code: "invalid_origin", message: "Invalid request origin." } });
    }
    const origin = originCheck.origin;
    if (request.method === "OPTIONS") {
      return new Response(null, {
        status: 204,
        headers: corsHeaders(origin),
      });
    }
    if (request.method !== "POST") {
      return jsonResponse(405, { error: { code: "method_not_allowed", message: "Only POST requests are accepted." } }, origin, {
        Allow: "POST",
      });
    }

    if (!isConfigured(env)) {
      return jsonResponse(503, unavailableError("connector_not_configured"), origin);
    }

    const body = await readJsonBody(request);
    if (!body.ok) {
      return jsonResponse(body.status, { error: body.error }, origin);
    }
    const payload = body.value;

    const validated = operation === "chat"
      ? validateChatPayload(payload)
      : operation === "presence"
        ? validatePresencePayload(payload)
        : validateMessagesPayload(payload);
    if ("error" in validated) {
      return jsonResponse(422, { error: validated.error }, origin);
    }

    const address = request.headers.get("CF-Connecting-IP") ?? "unknown";
    const rateLimitChecks: Array<[RateLimiter | undefined, string]> = operation === "presence"
      ? [
          [env.PRESENCE_LIMITER, `presence-visitor|${validated.value.visitor_id}`],
          ...(env.PRESENCE_SOURCE_LIMITER
            ? [[env.PRESENCE_SOURCE_LIMITER, `presence-source|${address}`] as [RateLimiter, string]]
            : []),
          ...(env.PRESENCE_SITE_LIMITER
            ? [[env.PRESENCE_SITE_LIMITER, "presence-site"] as [RateLimiter, string]]
            : []),
        ]
      : [[operation === "chat" ? env.CHAT_LIMITER : env.PRESENCE_LIMITER, `${operation}|${address}`]];
    for (const [limiter, keyMaterial] of rateLimitChecks) {
      const rateLimited = await checkRateLimit(limiter, keyMaterial, env.AGENT_SITE_KEY);
      if (rateLimited === null) {
        return jsonResponse(503, unavailableError("rate_limit_unavailable"), origin);
      }
      if (rateLimited) {
        return jsonResponse(429, { error: { code: "rate_limited", message: "Too many support requests. Please wait and try again." } }, origin);
      }
    }

    const upstream = await postToAgent(operation, validated.value, env, request);
    if (upstream.kind === "rate_limited") {
      return jsonResponse(429, { error: { code: "upstream_rate_limited", message: "Too many support requests. Please wait and try again." } }, origin);
    }
    if (upstream.kind !== "ok") {
      return jsonResponse(502, unavailableError("upstream_unavailable"), origin);
    }
    if (operation === "presence") {
      return jsonResponse(200, { status: "ok" }, origin);
    }
    if (operation === "messages") {
      return jsonResponse(200, publicHumanMessagesResponse(upstream.value), origin);
    }
    return jsonResponse(200, publicChatResponse(upstream.value), origin);
  },
};

function operationForPath(pathname: string): Operation | null {
  if (pathname === "/support-agent/chat" || pathname === "/support-agent/chat/") {
    return "chat";
  }
  if (pathname === "/support-agent/presence" || pathname === "/support-agent/presence/") {
    return "presence";
  }
  if (pathname === "/support-agent/messages" || pathname === "/support-agent/messages/") {
    return "messages";
  }
  return null;
}

function validateOrigin(request: Request, env: Env): { allowed: boolean; origin: string | null } {
  const configured = normalizeOrigin(env.PUBLIC_ORIGIN ?? "");
  const requestOrigin = normalizeOrigin(request.headers.get("Origin") ?? "");
  if (!configured) {
    return { allowed: false, origin: null };
  }
  if (requestOrigin === configured) {
    return { allowed: true, origin: configured };
  }
  if (!requestOrigin && env.ALLOW_MISSING_ORIGIN === "true") {
    return { allowed: true, origin: configured };
  }
  return { allowed: false, origin: null };
}

function normalizeOrigin(value: string): string | null {
  try {
    const parsed = new URL(value);
    if (parsed.protocol !== "https:" && parsed.protocol !== "http:") {
      return null;
    }
    if (parsed.username || parsed.password || parsed.pathname !== "/" || parsed.search || parsed.hash) {
      return null;
    }
    return parsed.origin;
  } catch {
    return null;
  }
}

function isConfigured(env: Env): boolean {
  if (!env.AGENT_API_BASE_URL || !env.AGENT_SITE_KEY || env.AGENT_SITE_KEY.length < 32) {
    return false;
  }
  const apiUrl = normalizeOrigin(env.AGENT_API_BASE_URL);
  if (!apiUrl) {
    return false;
  }
  return normalizeOrigin(env.PUBLIC_ORIGIN ?? "") !== null
    && (new URL(apiUrl).protocol === "https:" || env.ALLOW_INSECURE_AGENT_API === "true");
}

async function checkRateLimit(
  limiter: RateLimiter | undefined,
  keyMaterial: string,
  secret: string,
): Promise<boolean | null> {
  if (!limiter) {
    return null;
  }
  try {
    const key = await hmacFingerprint(keyMaterial, secret);
    const result = await limiter.limit({ key });
    return !result.success;
  } catch {
    return null;
  }
}

async function hmacFingerprint(value: string, secret: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(value));
  return [...new Uint8Array(signature)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

type BodyResult =
  | { ok: true; value: Record<string, unknown> }
  | { ok: false; status: 400 | 413; error: { code: string; message: string } };

async function readJsonBody(request: Request): Promise<BodyResult> {
  const declaredLength = Number(request.headers.get("Content-Length") ?? 0);
  if (declaredLength > MAX_BODY_BYTES) {
    return { ok: false, status: 413, error: { code: "payload_too_large", message: "Request body is too large." } };
  }
  const body = await request.arrayBuffer();
  if (body.byteLength > MAX_BODY_BYTES) {
    return { ok: false, status: 413, error: { code: "payload_too_large", message: "Request body is too large." } };
  }
  if (body.byteLength === 0) {
    return { ok: false, status: 400, error: { code: "invalid_json", message: "A valid JSON object is required." } };
  }
  try {
    const value: unknown = JSON.parse(new TextDecoder().decode(body));
    if (typeof value !== "object" || value === null || Array.isArray(value)) {
      return { ok: false, status: 400, error: { code: "invalid_json", message: "A valid JSON object is required." } };
    }
    return { ok: true, value: value as Record<string, unknown> };
  } catch {
    return { ok: false, status: 400, error: { code: "invalid_json", message: "A valid JSON object is required." } };
  }
}

function validateChatPayload(payload: Record<string, unknown>): ValidationResult {
  const message = typeof payload.message === "string" ? payload.message.trim() : "";
  if (!message || message.length > MAX_MESSAGE_LENGTH) {
    return { error: { code: "invalid_message", message: "Message must contain between 1 and 10000 characters." } };
  }
  const conversationId = optionalOpaqueId(payload.conversation_id, "conversation_id");
  if ("error" in conversationId) {
    return conversationId;
  }
  const pagePath = typeof payload.page_path === "string" ? payload.page_path.trim() : "/";
  if (!pagePath.startsWith("/") || pagePath.startsWith("//") || pagePath.length > MAX_PAGE_PATH_LENGTH) {
    return { error: { code: "invalid_page_path", message: "Page path must be a relative path." } };
  }
  return {
    value: {
      message,
      page_path: pagePath,
      ...(conversationId.value ? { conversation_id: conversationId.value } : {}),
    },
  };
}

function validatePresencePayload(payload: Record<string, unknown>): ValidationResult {
  const visitorId = requiredOpaqueId(payload.visitor_id, "visitor_id");
  if ("error" in visitorId) {
    return visitorId;
  }
  const pagePath = typeof payload.page_path === "string" ? payload.page_path.trim() : "";
  if (!pagePath || !pagePath.startsWith("/") || pagePath.length > MAX_PAGE_PATH_LENGTH) {
    return { error: { code: "invalid_page_path", message: "Page path must be a relative path." } };
  }
  const conversationId = optionalOpaqueId(payload.conversation_id, "conversation_id");
  if ("error" in conversationId) {
    return conversationId;
  }
  const optionalFields = {
    page_title: boundedString(payload.page_title, 200),
    referrer: boundedString(payload.referrer, 1000),
    language: boundedString(payload.language, 35),
    timezone: boundedString(payload.timezone, 100),
  };
  const pageViewId = optionalOpaqueId(payload.page_view_id, "page_view_id");
  if ("error" in pageViewId) {
    return pageViewId;
  }
  const widgetState = payload.widget_state === undefined
    ? undefined
    : boundedEnum(payload.widget_state, ["closed", "open"]);
  const presenceSource = payload.presence_source === undefined
    ? undefined
    : boundedEnum(payload.presence_source, ["page_load", "widget"]);
  if (widgetState === null || presenceSource === null) {
    return { error: { code: "invalid_presence_state", message: "Presence state is invalid." } };
  }
  return {
    value: {
      visitor_id: visitorId.value,
      page_path: pagePath,
      ...(conversationId.value ? { conversation_id: conversationId.value } : {}),
      ...(pageViewId.value ? { page_view_id: pageViewId.value } : {}),
      ...(widgetState ? { widget_state: widgetState } : {}),
      ...(presenceSource ? { presence_source: presenceSource } : {}),
      ...Object.fromEntries(Object.entries(optionalFields).filter(([, value]) => value)),
    },
  };
}

function validateMessagesPayload(payload: Record<string, unknown>): ValidationResult {
  const conversationId = requiredOpaqueId(payload.conversation_id, "conversation_id");
  if ("error" in conversationId) {
    return conversationId;
  }
  return { value: { conversation_id: conversationId.value } };
}

interface ValidationError {
  code: string;
  message: string;
}

type ValidationResult = { value: Record<string, string> } | { error: ValidationError };

type OpaqueResult = { value: string } | { error: ValidationError };

type OptionalOpaqueResult = { value?: string } | { error: ValidationError };

function requiredOpaqueId(value: unknown, field: string): OpaqueResult {
  if (typeof value !== "string" || !OPAQUE_ID_PATTERN.test(value)) {
    return { error: { code: `invalid_${field}`, message: `${field} must be an opaque identifier.` } };
  }
  return { value };
}

function optionalOpaqueId(value: unknown, field: string): OptionalOpaqueResult {
  if (value === undefined || value === null || value === "") {
    return { value: undefined };
  }
  return requiredOpaqueId(value, field);
}

function boundedString(value: unknown, maximumLength: number): string | undefined {
  if (typeof value !== "string") {
    return undefined;
  }
  const normalized = value.trim();
  return normalized ? normalized.slice(0, maximumLength) : undefined;
}

function boundedEnum(value: unknown, allowed: string[]): string | null {
  return typeof value === "string" && allowed.includes(value) ? value : null;
}

async function postToAgent(
  operation: Operation,
  payload: Record<string, string>,
  env: Env,
  request: Request,
): Promise<{ kind: "ok"; value: Record<string, unknown> } | { kind: "rate_limited" } | { kind: "error" }> {
  const base = env.AGENT_API_BASE_URL.replace(/\/+$/, "");
  const path = operation === "chat"
    ? "/v1/widget/chat"
    : operation === "presence"
      ? "/v1/widget/presence"
      : "/v1/widget/messages";
  const timeout = operation === "chat" ? 25_000 : 8_000;
  try {
    const response = await fetch(`${base}${path}`, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-Agent-Site-Key": env.AGENT_SITE_KEY,
        "X-Agent-Visitor-IP": request.headers.get("CF-Connecting-IP") ?? "",
        "X-Agent-Visitor-Country": request.headers.get("CF-IPCountry") ?? "",
        "X-Agent-Visitor-User-Agent": request.headers.get("User-Agent") ?? "",
        "User-Agent": "CompanyProductSupportAgentCloudflareConnector/0.1.0",
      },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(timeout),
    });
    if (response.status === 429) {
      return { kind: "rate_limited" };
    }
    if (!response.ok) {
      return { kind: "error" };
    }
    const value: unknown = await response.json();
    return typeof value === "object" && value !== null && !Array.isArray(value)
      ? { kind: "ok", value: value as Record<string, unknown> }
      : { kind: "error" };
  } catch {
    return { kind: "error" };
  }
}

function publicChatResponse(response: Record<string, unknown>): Record<string, unknown> {
  const citations = Array.isArray(response.citations)
    ? response.citations.filter((citation): citation is string => typeof citation === "string")
    : [];
  const relatedLinks = Array.isArray(response.related_links)
    ? response.related_links.filter(
        (relatedLink): relatedLink is string => typeof relatedLink === "string",
      )
    : [];
  return {
    conversation_id: typeof response.conversation_id === "string" ? response.conversation_id : "",
    message: typeof response.message === "string" ? response.message : "",
    kind: typeof response.kind === "string" ? response.kind : "handoff",
    risk_level: typeof response.risk_level === "number" ? response.risk_level : 0,
    handoff_id: typeof response.handoff_id === "string" ? response.handoff_id : null,
    citations,
    related_links: relatedLinks,
  };
}

function publicHumanMessagesResponse(response: Record<string, unknown>): Record<string, unknown> {
  const items = Array.isArray(response.items)
    ? response.items
      .filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null && !Array.isArray(item))
      .filter((item) => typeof item.message_id === "string" && typeof item.content === "string")
      .map((item) => ({
        message_id: item.message_id,
        content: item.content,
        created_at: typeof item.created_at === "string" ? item.created_at : "",
      }))
    : [];
  return { items };
}

function unavailableError(code: string): Record<string, unknown> {
  return { error: { code, message: "Support is temporarily unavailable." } };
}

function corsHeaders(origin: string | null): Headers {
  const headers = new Headers({
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    Vary: "Origin",
  });
  if (origin) {
    headers.set("Access-Control-Allow-Origin", origin);
    headers.set("Access-Control-Allow-Methods", "POST, OPTIONS");
    headers.set("Access-Control-Allow-Headers", "Content-Type");
    headers.set("Access-Control-Max-Age", "600");
  }
  return headers;
}

function jsonResponse(
  status: number,
  payload: Record<string, unknown>,
  origin: string | null = null,
  extraHeaders: Record<string, string> = {},
): Response {
  const headers = corsHeaders(origin);
  headers.set("Content-Type", "application/json; charset=utf-8");
  for (const [key, value] of Object.entries(extraHeaders)) {
    headers.set(key, value);
  }
  return new Response(JSON.stringify(payload), { status, headers });
}
