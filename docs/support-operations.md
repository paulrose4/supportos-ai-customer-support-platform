# Support Operations Control Plane

## Scope

The support operations control plane turns the AI runtime into an operator-facing customer
support platform. It provides tenant-scoped sites, a unified inbox, conversation workspaces,
human takeover, release back to AI, resolution, agent replies, and durable customer memory.
It is intentionally API-first; a separate dashboard frontend can consume these contracts
without importing backend or infrastructure models.

## Conversation State

Conversation lifecycle and ownership are independent:

- `status`: `open`, `waiting_human`, or `resolved`.
- `ownership_mode`: `ai`, `queued`, or `human`.
- `assigned_agent_id`: present only when a trusted operator owns the conversation.
- `risk_level`: the highest known deterministic/agent risk level.
- `unread_count`: operator-facing unread activity count.

This avoids overloading a single status field with lifecycle, queue, and assignment semantics.
A handoff moves an AI-owned conversation to `waiting_human + queued`. Taking over moves it to
`open + human`; resolving moves it to `resolved + human` and resolves pending/assigned handoffs.

## HTTP API

- `GET /v1/admin/sites`
- `GET /v1/admin/inbox`
- `GET /v1/admin/customers`
- `GET /v1/admin/customers/{customer_id}/conversations`
- `GET /v1/admin/conversations/{conversation_id}`
- `POST /v1/admin/conversations/{conversation_id}/takeover`
- `POST /v1/admin/conversations/{conversation_id}/release-to-ai`
- `POST /v1/admin/conversations/{conversation_id}/resolve`
- `POST /v1/admin/conversations/{conversation_id}/messages`
- `POST /v1/admin/conversations/{conversation_id}/notes`
- `POST /v1/admin/conversations/{conversation_id}/routing`
- `POST /v1/admin/conversations/{conversation_id}/handoffs`
- `POST /v1/admin/conversations/{conversation_id}/read`
- `GET /v1/admin/support-configuration`
- `POST /v1/admin/canned-replies`
- `GET /v1/admin/customers/{customer_id}/memory`
- `PUT /v1/admin/customers/{customer_id}/memory`
- `DELETE /v1/admin/customers/{customer_id}/memory/{memory_id}`

All writes require an explicit `idempotency_key`. Browser-supplied tenant or actor identities are
ignored; the administrative authentication adapter supplies both.

## Write Governance

| Write | Permission | Idempotency | Transaction | Audit | Human approval |
| --- | --- | --- | --- | --- | --- |
| Take over | `support:inbox:write` | Tenant + key, one operation/resource | Row lock, assignment, handoff update, audit, idempotency record | `conversation.taken_over` | Authorized agent action is approval |
| Release to AI | `support:inbox:write` | Tenant + key | State transition, audit and idempotency record in one transaction | `conversation.released_to_ai` | Authorized agent action is approval |
| Resolve | `support:inbox:write` | Tenant + key | Conversation and open handoffs resolve atomically | `conversation.resolved` | Human-only in MVP; AI cannot call this port |
| Agent reply | `support:inbox:write` plus active assignment | Deterministic message ID from key | Ownership rechecked under row lock; message and audit commit together | `conversation.agent_message_sent` | Authorized assigned agent action is approval |
| Internal note | `support:inbox:write` | Deterministic message ID from key | Operator-only message and audit commit together | `conversation.internal_note_added` | Authorized agent action is approval |
| Routing update | `support:inbox:write` | Tenant + key | Agent/queue eligibility, priority, tags and audit commit together | `conversation.routing_updated` | Authorized agent action is approval |
| Manual handoff | `support:inbox:write` | Tenant + key; an existing open handoff is reused | Handoff context, conversation queue state and audit commit together | `handoff.created_manually` | Authorized agent action is approval |
| Mark read | `support:inbox:read` | Tenant + key | Unread count and read timestamp commit with audit | `conversation.read` | No additional approval |
| Memory upsert | `customers:memory:write` | Deterministic memory ID from tenant, customer and key | Trusted-customer check, memory and audit commit together | `customer_memory.upserted` | Human-only in MVP; future LLM candidates require review |
| Memory delete | `customers:memory:write` | Tenant + key | Delete and audit commit together | `customer_memory.deleted` | Authorized user request or operator action |

An idempotency key cannot be reused for another operation or resource. Concurrent takeover and
reply operations use database row locks and recheck ownership inside the transaction.

## Customer Memory Boundary

Durable memory is stored in PostgreSQL and scoped by `tenant_id + trusted customer_id`.
Qdrant remains knowledge-only. The MVP accepts only whitelisted memory kinds, trusted sources,
confidence from `0.8` to `1.0`, and explicit `granted` consent. Passwords, cards, tokens,
verification codes, and security answers are rejected deterministically.

Orders, payments, permissions, inventory, and other live business facts are never read from
memory. Those facts must be queried from authoritative business adapters at answer time.

## Dashboard Workflow

The frontend should be a separate application with:

1. Site/property switcher.
2. Unified inbox filters and queue counts.
3. Three-column conversation workspace.
4. Human takeover/release/resolve controls.
5. Customer memory and business-data side panel.
6. Structured handoff context with intent, unresolved question, AI attempt, evidence, recommended action and editable reply draft.
7. Queue, assignment, priority, tags, internal notes and tenant-scoped canned replies.
8. Server-backed customer directory and customer conversation history independent of inbox limits.
9. WebSocket updates using the same application services for writes.

Internal notes use `message_type=internal_note` and `visibility=operators_only`. Public Widget
message retrieval continues to select only agent messages with `message_type=chat`, so internal
notes cannot cross the visitor boundary. Handoff pagination is server-side and ordered by
`created_at + handoff_id`; the Dashboard no longer intersects tickets with the bounded inbox.

## Customer Experience Workflows

The customer-experience application service extends the same tenant-scoped conversation and
ticket state instead of creating a parallel workflow.

### Versioned Widget Configuration

- `GET /v1/admin/customer-experience/sites/{site_id}/widget-config`
- `POST /v1/admin/customer-experience/sites/{site_id}/widget-config/drafts`
- `POST /v1/admin/customer-experience/sites/{site_id}/widget-config/publish`
- `POST /v1/admin/customer-experience/sites/{site_id}/widget-config/rollback`
- `GET /v1/admin/customer-experience/sites/{site_id}/widget-assets`
- `POST /v1/admin/customer-experience/sites/{site_id}/widget-assets?purpose=launcher|avatar`

Every save creates a new draft version. A draft never affects visitors. Publishing archives the
previous published version under a site row lock. Rollback copies the selected historical config
into a new published version, preserving the complete history. Public bootstrap returns only the
published version plus a server-calculated business-hours state.

The published config controls welcome/offline copy, timezone and weekday ranges, holidays,
offline form, primary color, launcher position, agent identity, mobile visibility, language,
handoff timeout, and CSAT visibility. Offline form submission creates a normal queued
conversation and support ticket; it does not use a second message system.

### Deterministic Automation

- `GET|POST /v1/admin/customer-experience/automation/rules`
- `DELETE /v1/admin/customer-experience/automation/rules/{rule_id}`
- `POST /v1/admin/customer-experience/automation/test`
- `GET /v1/admin/customer-experience/automation/executions`

Conditions are a fixed schema: site, page prefix, business-hours state, supported intent,
minimum risk, authentication state, dwell time, assignment state, and ticket state. Actions are
restricted to queue, priority, tags, ticket creation, and direct handoff. Unknown fields are
rejected. There is no arbitrary script, refund, cancellation, payment, identity, or other
business-mutation action.

Direct handoff and support-ticket creation are separate actions. A direct handoff creates or
reuses `handoff_requests` context and moves the conversation to the human queue. A support ticket
is created only when `create_ticket` is explicitly selected or an offline asynchronous request
requires follow-up. Resolving a conversation therefore does not implicitly close a support ticket.

Each enabled rule is evaluated in stable sort order after a Widget exchange is persisted. Every
evaluation records matched/not-matched reasons and actual applied actions. Execution IDs and
idempotency keys are deterministic per rule and Widget request.

### CSAT And Knowledge Gaps

- `POST /v1/public-widget/satisfaction`
- `GET /v1/public-widget/appearance?public_widget_id=...`
- `GET /v1/widget-media/{asset_id}?size=64|128|256`
- `GET /v1/admin/customer-experience/knowledge-gaps`
- `POST /v1/admin/customer-experience/conversations/{conversation_id}/knowledge-gaps`
- `POST /v1/admin/customer-experience/knowledge-gaps/{gap_id}/resolve`
- `GET /v1/admin/customer-experience/summary`

The Widget displays CSAT only after the trusted conversation state is `resolved`. One rating is
accepted per tenant conversation. Operators can classify a handoff as missing knowledge or an
incorrect AI answer. A gap remains open until an authorized operator records a resolution note;
the Dashboard report exposes the average score, rating count, and open-gap count.

## Tenant Experience Learning Operations

Experience jobs always use tenant IDs from trusted command arguments or
`TENANT_EXPERIENCE_WORKER_TENANT_IDS`. There is no ordinary cross-tenant memory scan; the worker
enters an isolated tenant scope and completes one tenant before moving to the next.

```powershell
python scripts/run_tenant_experience_worker.py serve
python scripts/run_tenant_experience_worker.py --tenant-id tenant-demo serve --once
python scripts/run_tenant_experience_worker.py --tenant-id tenant-demo cycle
python scripts/run_tenant_experience_worker.py --tenant-id tenant-demo record-eval `
  --report .\artifacts\experience-eval.json --dataset-version replay-2026-07-31
python scripts/run_tenant_experience_worker.py --tenant-id tenant-demo activate `
  --approved-by operator-subject-id --rollout-percent 5 `
  --include-memory-id approved-memory-id `
  --eligible-intent product_care --eligible-site site-a `
  --risk-ceiling 1 --dataset-version replay-2026-07-31
python scripts/run_tenant_experience_worker.py --tenant-id tenant-demo rollback `
  --actor-subject-id operator-subject-id
python scripts/run_tenant_experience_worker.py --tenant-id tenant-demo delete-conversation `
  --conversation-id conversation-id
python scripts/run_tenant_experience_worker.py --tenant-id tenant-demo invalidate `
  --dependency-kind knowledge_version_ids --dependency-value obsolete-version-id
```

`serve` is the continuous production mode used by the `tenant-experience-worker` Compose service.
It executes a cycle for each configured tenant, waits
`TENANT_EXPERIENCE_WORKER_POLL_SECONDS`, and repeats. The default release remains Shadow with zero
rollout, so running the worker does not activate experience guidance.

`cycle` reconciles delayed outcomes, enforces online release guardrails, builds redacted cases,
consolidates repeated tenant-local patterns, creates draft improvement candidates, and evaluates
the current release. It never activates memories. Pattern consolidation requires distinct hashed
customer or visitor cohorts, so repeated conversations from one actor cannot satisfy the minimum
cluster size.

`record-eval` requires all safety and quality metrics used by the release gate. `activate` fails
unless the gate status is `passed` and the requested dataset version matches that evaluation. The
activation creates an immutable manifest containing the exact memory IDs, exclusions, sites,
intents, risk ceiling, and dataset version. Omitting `--include-memory-id` includes every currently
eligible Shadow or active memory; explicit IDs are recommended for production canaries.

Evaluation reports use metrics schema version 2. They must include a SHA-256 dataset fingerprint,
deduplicated case count, minimum per-segment sample count, issue-segmentation accuracy,
outcome-label accuracy, baseline release version, and the exact evaluated memory IDs in addition to
the safety and quality metrics. Activation rejects any memory absent from that evaluated set.

The worker attributes outcomes only to guidance that actually influenced a treatment response.
After both cohorts reach `TENANT_EXPERIENCE_GUARDRAIL_MINIMUM_SAMPLES`, a treatment failure-rate
regression above `TENANT_EXPERIENCE_MAXIMUM_FAILURE_RATE_DELTA` automatically pauses the release.
Individual case memories exceeding `TENANT_EXPERIENCE_MAXIMUM_NEGATIVE_TRANSFER_RATE` are isolated
without disabling unrelated memories. `rollback` immediately excludes the manifest and returns its
operational cases to Shadow.
`delete-conversation` removes the issue lineage, cases, vectors, usages, events, summaries, and
experiment assignment, then disables affected Patterns and marks their draft candidates for rebuild.
`invalidate` marks only Cases with matching dependency fingerprints as stale and rebuilds dependent
Patterns and candidates; it does not invalidate unrelated tenant experience.
