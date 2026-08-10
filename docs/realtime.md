# Realtime Support Events

## Current Design

`InMemoryRealtimeHub` implements the domain `EventPublisherPort` and delivers tenant-scoped support events to `WS /v1/ws/support`. WebSocket authentication uses the same HttpOnly administrator session cookie as the Dashboard. The tenant comes from the authenticated principal, never the WebSocket message body.

Published MVP events include:

- `conversation.ai_exchange_persisted`
- `conversation.taken_over`
- `conversation.released_to_ai`
- `conversation.resolved`
- `conversation.agent_message_sent`
- `customer_memory.upserted`
- `customer_memory.deleted`

Queues are bounded. When a slow subscriber fills its queue, the oldest event is dropped so application writes are not blocked. PostgreSQL remains the durable source of truth; every event tells the Dashboard to refresh the relevant REST projection rather than treating the event payload as authoritative business state.

## Failure Behavior

- Invalid or expired administrator sessions are rejected before subscription.
- Subscribers receive only events published for their authenticated tenant.
- WebSocket disconnects do not roll back completed application transactions.
- The Dashboard reconnects with a bounded delay and refreshes the inbox after relevant events.
- A missed in-memory event is recovered by the next REST refresh.

## Scaling Boundary

The in-memory hub is suitable only for one API process. Multiple workers or replicas require a shared broker adapter such as Redis Streams/PubSub or NATS JetStream. That adapter must continue to implement `EventPublisherPort`, preserve tenant subjects, bound delivery/retry behavior, and avoid placing secrets or unrestricted customer payloads on the bus.

Recommended production path:

1. Add a transactional PostgreSQL outbox record in the same write transaction.
2. Publish outbox events to tenant-partitioned Redis/NATS subjects.
3. Consume through per-instance WebSocket hubs.
4. Track delivery lag, reconnect count, queue drops, and broker failures.
5. Keep REST reload as the reconciliation path.


## Visitor Presence

`POST /v1/public-widget/presence` and `POST /v1/widget/presence` record authenticated page heartbeats in a tenant-isolated presence store. The public endpoint issues a 15-minute Presence-only token without creating a PostgreSQL visitor session; trusted connectors continue to use the server-side site key. Both paths obtain tenant and site identity from trusted mappings. Heartbeats are idempotent upserts by tenant, site, anonymous visitor ID, and `page_view_id`. A visitor is online when its last heartbeat is within 60 seconds, is shown as recently left for up to 5 minutes, and expires from Redis after 6 minutes.

The request adapter supplies the observed source IP, Cloudflare country code, and browser user agent. Trusted WordPress, static PHP, and Cloudflare Worker connectors forward those values in site-key-authenticated headers. Browser JSON may supply only display metadata such as page title, referrer, language, and timezone; it cannot supply identity, tenant, site, or IP address. The store preserves the first heartbeat and increments page views when the relative path changes.

`GET /v1/admin/presence` requires an authenticated administrator with `support:inbox:read`. Presence is intentionally not persisted or audited because it is short-lived telemetry and performs no customer, order, payment, ticket, or knowledge write. The in-process lock provides atomic replacement, but there is no database transaction or human approval.

Production uses Redis item keys plus a tenant ZSET index for cross-instance visibility. Heartbeats update the item, TTL, and index in one transaction; listing uses score ranges and `MGET`, never a keyspace `SCAN`. The in-memory adapter remains available for local tests and controlled single-process use.
## Production transport

Production uses PostgreSQL transactional outbox events and Redis Streams. API workers do not publish in-process events when `REDIS_URL` is configured. Audit events are inserted in the same transaction as the business write, a database trigger creates the outbox row, and `scripts/run_outbox_worker.py` retries delivery with a lease. Redis Streams are retained for bounded replay; WebSocket reconnects must reconcile through the REST inbox APIs.

Presence and Widget rate limits use Redis TTL/atomic counters. The in-memory adapters remain available only for local tests and controlled single-process trials.
