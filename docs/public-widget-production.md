# Public Widget Production Runtime

This document is the release contract for operating the public Widget across 30 or more sites. It
does not turn the single-host Compose baseline into a highly available deployment. Infrastructure
owners must satisfy the topology and load gates below before broad rollout.

## Preserved business boundaries

- PostgreSQL remains the source of truth for conversations, messages, handoff state, quotas,
  visitor sessions, and trusted site registration.
- Redis is an acceleration, coordination, and event-delivery dependency. It is never an
  authorization source. New AI chat fails closed when Redis-backed capacity or model budgets are
  unavailable.
- Qdrant contains rebuildable knowledge projections only. Tenant, site, active generation, and
  audience filters remain mandatory.
- `tenant_id` and `site_id` come from the trusted site registry and signed Widget token, never from
  request text, browser storage, or model output.
- No database transaction may remain open while a model request is running.

## Request lifecycle

1. Page load mounts the fixed-size local launcher, requests the published appearance document with
   Origin validation, reuses a random first-party visitor cookie, and sends a lightweight Presence
   `enter` request while the page is visible. Browsers revalidate Appearance on every page load;
   shared caches may retain it for 60 seconds using its ETag. This does not bootstrap chat or create
   a durable visitor session. The built-in chat icon remains visible until a managed launcher image
   has decoded successfully.
2. The first click loads `widget-runtime.js` and `widget.css`, then calls chat bootstrap once.
3. Token v2 uses a stable `sid` for conversation ownership and a rotating `jti` for each 15-minute
   access token. A 30-day opaque resume token is stored only as a server-side hash and rotates on
   use. The visitor cookie is not an authorization credential.
4. Presence token v3 is scoped only to `presence:write` and binds the public Widget ID, exact Origin,
   auth version, and anonymous visitor hash. Presence runs while the page is visible and online,
   regardless of panel state. One visible tab owns a five-second renewable lease; heartbeats are
   randomized from 20 to 25 seconds. SPA navigation creates a new idempotency ID and reports at once.
5. Human messages are queried only while handoff is active. The PostgreSQL message ID is wrapped in
   a signed cursor bound to tenant, site, session, and conversation.
6. During handoff, a conversation-scoped POST SSE stream wakes the client. PostgreSQL cursor reads
   recover missed data; SSE is not the source of truth. Polling falls back with bounded backoff.

## Admission and overload behavior

Admission order is edge filtering, visitor/IP rate limits, atomic site daily quota, global/site
capacity lease, then the model gateway budget. Daily quota counting is idempotent by `request_id`.
Capacity waits are bounded; saturation returns `503` with `Retry-After`, while quota and rate limits
return `429`. Capacity leases expire in Redis and are also released in the API `finally` path.

Initial settings for one API replica are:

```dotenv
WIDGET_CHAT_RATE_LIMIT_PER_MINUTE=6
WIDGET_PRESENCE_RATE_LIMIT_PER_MINUTE=6
WIDGET_PRESENCE_SOURCE_RATE_LIMIT_PER_MINUTE=120
WIDGET_PRESENCE_SITE_RATE_LIMIT_PER_MINUTE=30000
REDIS_PRESENCE_TTL_SECONDS=360
WIDGET_CHAT_GLOBAL_CONCURRENCY=100
WIDGET_CHAT_SITE_CONCURRENCY=20
WIDGET_CHAT_CAPACITY_WAIT_SECONDS=1.5
WIDGET_CHAT_CAPACITY_LEASE_SECONDS=90
WIDGET_CHAT_DEADLINE_SECONDS=30
MODEL_GATEWAY_REQUESTS_PER_MINUTE=3000
MODEL_GATEWAY_TOKENS_PER_MINUTE=3000000
```

These are safety defaults, not capacity evidence. Set global concurrency from measured model
latency and provider limits. For a target of 10 chat requests/second at 8 seconds average latency,
start near `10 * 8 * 1.2 = 96` slots, then validate provider RPM/TPM and database impact.

## Database and PgBouncer

Each API replica starts with `pool_size=10`, `max_overflow=5`, a 2-second pool wait, pre-ping, and
900-second recycling. Budget total possible connections across API and worker replicas before
choosing the PostgreSQL server limit.

Use PgBouncer transaction pooling once API replicas exceed the direct connection budget. Set
`DATABASE_PREPARED_STATEMENT_CACHE_SIZE=0` for asyncpg compatibility. Keep the existing
transaction-local tenant `set_config` hook; never replace it with connection-level `SET`. Run all
RLS integration tests through PgBouncer before cutover. Conversation, quota, and handoff reads must
use the primary database; replicas are for reporting only.

## Required production topology

- At least three one-worker API containers across two hosts or availability zones.
- External load balancer with health-based removal and SSE streaming support.
- HA PostgreSQL with PITR and PgBouncer, HA Redis, and replicated or managed Qdrant.
- Crawl, embedding, reranking, outbox, and experience workers isolated from API CPU and memory.
- Versioned Widget assets cached by CDN for one year; WAF bot score, body limits, and IP/ASN anomaly
  protection in front of public endpoints.
- Prometheus scraping every API replica. Do not attach conversation, customer, IP, or arbitrary
  site values as metric labels.

## Managed image storage and CDN

Widget configuration versions store only tenant- and site-scoped asset IDs. PostgreSQL stores image
metadata and upload audit events, never image bytes. PNG, JPEG, and WebP uploads are limited to 2 MB
by default, decoded to verify their real format, constrained to 32-4096 pixels, stripped of EXIF,
and re-encoded into immutable 64, 128, and 256 pixel WebP variants. SVG is deliberately rejected.

Development may use `WIDGET_ASSET_STORAGE_BACKEND=filesystem`. A single-host production fallback
uses the persistent `widget_assets` Compose volume and `/app/data/widget-assets`; include it in the
backup and restore procedure. Three-replica production must use a private S3-compatible bucket:

```dotenv
WIDGET_ASSET_STORAGE_BACKEND=s3
WIDGET_ASSET_S3_BUCKET=customer-support-widget-assets
WIDGET_ASSET_S3_PREFIX=production/widget-assets
WIDGET_ASSET_S3_REGION=auto
# Set WIDGET_ASSET_S3_ENDPOINT_URL for R2, MinIO, or another compatible service.
```

Prefer workload identity or an instance role. Configure access key variables only when the runtime
has no workload identity, and keep them in the secret manager. The bucket remains private; browsers
read `/v1/widget-media/{asset_id}` through the API/CDN. Cache `/v1/widget-media/*` for one year and
respect its immutable response header. Cache `/v1/public-widget/appearance` using its ETag,
`Vary: Origin`, and `stale-while-revalidate`; do not cache 403 or 429 responses.

Sites with CSP must add the support API origin to `script-src`, `style-src`, `connect-src`, and
`img-src`. No arbitrary operator-supplied image host is required. A missing, corrupt, timed-out, or
retired image must leave the built-in chat icon visible.

## Configuration authority and connector migration

The Dashboard-published `WidgetConfigVersion` is the default appearance authority for every
connector. A public site ID selects the registered site; it never selects `tenant_id` directly.
WordPress 0.4.0 resolves that public ID through `GET /v1/widget/manifest` using the private site key
server-side, then emits only the public ID and the immutable asset version to the browser. Static PHP
and Cloudflare deployments should use the same hosted script contract shown in their embed examples.

Keep the outbox worker running in every environment that permits publishing. Publish and rollback
transactions enqueue `widget_config.published` or `widget_config.rolled_back`; the worker deletes
only `public-widget:site:<public_widget_id>` before publishing the realtime event. Redis failure is
retried through the existing outbox lease, while the ten-second access-cache TTL remains a fallback.

The media URLs in Appearance and Bootstrap are absolute URLs built only from
`PUBLIC_WIDGET_BASE_URL`. Production must set this to the public HTTPS Widget origin. Never derive
media hosts from the visitor's `Host` header. Session state (`cpsa_session_*`) and appearance state
(`cpsa_appearance_*`) are intentionally separate, so an appearance refresh or rollback never clears
the visitor's conversation.

## Release and rollback

Apply migration `b8c9d0e1f2a3` before enabling managed images. New API fields are optional so the old
Widget remains compatible during a rolling deployment. Deploy API replicas and the outbox worker
before publishing the versioned Loader. Upgrade WordPress to 0.4.0 only after `/v1/widget/manifest`
is reachable from the WordPress server. Purge the CDN and WordPress page cache after the plugin
upgrade; the versioned Runtime and CSS may then remain immutable.

Feature rollback controls:

- `WIDGET_SSE_ENABLED=false` returns clients to incremental polling.
- `MODEL_GATEWAY_ENABLED=false` bypasses the Redis model budget only during a controlled rollback;
  provider-side limits and chat capacity must remain in force.
- Reverting Widget assets does not require a database downgrade. Do not downgrade the migration
  while v2 sessions or quota records are active.
- Set loader `presenceMode` (or `data-presence-mode`) to `widget_only` for the old behavior, or
  `disabled` to turn off Presence. Consent-gated sites set `presenceConsentRequired` and call
  `window.SupportOS.setPresenceConsent(true)` after consent.

## Capacity gate

The executable staging procedure, k6 shards, datastore audit, alert rules, fault drills, and
rollout controls are documented in
[`presence-capacity-runbook.zh-CN.md`](./presence-capacity-runbook.zh-CN.md).

Staging must run all PostgreSQL, Redis, and Qdrant integration tests without skips, followed by:

- 10,000 visible browsing clients at a 25-second heartbeat, approximately 400 Presence RPS.
- 2,000 open panels without duplicate Runtime Presence or empty human-message queries.
- 10 chat requests/second for 30 minutes and a provider-bounded 50 requests/second burst.
- 500 active handoff SSE connections with cursor recovery after reconnect.
- One site producing 40 percent of traffic without degrading other sites' P95 by more than 20
  percent.
- Redis delay/outage, provider 429, Qdrant outage, database pool exhaustion, and PostgreSQL failover.
- A 2-hour peak test and a 24-hour soak test.

Release targets are light endpoints P95 below 250 ms, chat P95 below 15 seconds with a 30-second
hard deadline, uncontrolled 5xx below 0.5 percent, database pool wait P95 below 50 ms, and outbox
lag below 5 seconds. Roll out to 3 sites, then 10, then 30. Stop expansion if queues grow, provider
429s persist, pool waits exceed target, or one site affects another.
