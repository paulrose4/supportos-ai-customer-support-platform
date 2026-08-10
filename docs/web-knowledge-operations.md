# Website Knowledge Operations

## Production objective

Website knowledge synchronization is an operations workflow, not a long-running browser request.
An administrator queues work in Dashboard, a dedicated worker performs the crawl, and PostgreSQL
stores the durable status, lease, report, and audit trail. The previous complete PostgreSQL and
Qdrant snapshot remains active until a new snapshot is fully validated and published.

## Delivered operating model

### Administrator workflow

1. Open **Knowledge** in Dashboard and select an active registered site.
2. Select **Re-detect scope**. The server reads the root sitemap index, excludes detected
   GTranslate sitemap suffixes and first-path language routes, and freezes the main-language URLs.
3. Review the main-language URL count, primary sitemaps, excluded locales, and blocking reasons.
4. Select a fixed 20, 100, 200, or 500 page sample and start a shadow validation.
5. Leave the page if needed. The API returns after the job is durably queued.
6. Return to review page-level progress, HTTP 304 reuse, retries, exclusions, and failures.
7. Use **Safe cancel** when required. The current page finishes, completed checkpoints remain
   durable, and unstarted pages are canceled without changing the active knowledge snapshot.
8. After the 20-page, 500-page, and full-manifest rollout gates pass, enable the deployment publication flag,
   select **Production sync**, review the full frozen URL count, and confirm the formal job.

The page reports effective pages, unique products, HTTP 304 reuse, new vectors, failures, and
duplicate product identities. Operators cannot edit tenant identity, base URL, sitemap limits,
crawl delay, or publication rules. Those values come from the trusted site registry and deployment
configuration.

### Sitemap source configuration

Each site has a persistent source configuration under **Settings > Knowledge sync source**:

- **Automatic discovery** reads `robots.txt`, the last complete manifest, and the supported common
  paths. All valid common-path roots are combined rather than accepting only the first match.
- **Manual first** validates up to ten configured sitemap URLs and falls back to automatic
  discovery with an operator-visible warning when the configured set is unavailable.
- **Manual only** blocks preflight when any configured root or indexed child cannot be read, because
  silently reducing the declared scope could publish incomplete knowledge.

Configured URLs may use dynamic paths and query strings. The filename and response MIME type are
advisory only; bounded XML parsing determines whether the response is a sitemap. A URL may use the
site's own HTTPS origin or another active, ownership-verified site origin in the same tenant. Page
URLs remain restricted to the selected site's origin even when a verified CDN hosts the sitemap.

`GET /v1/admin/site-management/{site_id}/web-source` returns the current `config_version` and
allowed sitemap origins. `PUT` requires `expected_config_version`; stale writes return HTTP 409 so
two operators cannot silently overwrite each other's changes. Successful updates and validation
status changes are tenant-scoped and audited.

Every frozen crawl manifest records the source `config_version` used during discovery. Creating a
new sync job locks the registered site and compares that version with the current configuration in
the same PostgreSQL transaction. A changed configuration returns HTTP 409 and requires **Re-detect
scope**; already queued or running jobs keep their immutable scope and are not canceled.

Production jobs stage each page under the durable job ID. PostgreSQL versions remain indexed but
inactive, Qdrant points carry the same snapshot ID with `is_active=false`, and product facts remain
in a staging snapshot. Finalization compares changed-version chunk manifests, Qdrant snapshot
points, and unique staged product keys before activating anything. Qdrant and PostgreSQL version
sets are switched first, the knowledge sync is recorded, and the product snapshot is activated
last. A product activation failure restores both prior knowledge version sets. A page failure or
safe cancellation discards inactive Qdrant points and fails the staging product snapshot.

### Runtime components

- `POST /v1/knowledge/web-crawl-preflights/{site_id}` creates an immutable main-language manifest.
- `GET /v1/knowledge/web-crawl-preflights/{site_id}/latest` returns the latest tenant-scoped manifest.
- `POST /v1/knowledge/web-sync-jobs/{site_id}` queues a manifest-bound job and returns HTTP 202.
- `GET /v1/knowledge/web-sync-jobs` lists tenant-scoped job history.
- `GET /v1/knowledge/web-sync-jobs/{job_id}` returns one job and its final report.
- `GET /v1/knowledge/web-sync-jobs/{job_id}/items` returns tenant-scoped page checkpoints.
- `POST /v1/knowledge/web-sync-jobs/{job_id}/cancel` requests a safe stop.
- `scripts/run_web_sync_worker.py` discovers active tenants from the trusted PostgreSQL tenant
  catalog, fairly leases their queued jobs, and executes bounded work time slices.
- `scripts/enqueue_web_sync_job.py` gives the deployment scheduler a non-browser entry point.
- `web_crawl_manifests` and `web_crawl_manifest_items` store the frozen scope and page validators.
- `web_sync_jobs` stores mode, manifest identity, status, lease, report, and sanitized failure.
- `web_sync_job_items` stores one durable URL checkpoint with its own lease, retry count,
  validators, duration, result, and sanitized failure.
- `web_sync_worker_registrations` stores tenant-scoped worker health, coverage, expiry, runtime
  generation, and current capacity. It is the API admission source; the file heartbeat is only a
  container health signal.

Only one queued or running job can exist for a tenant and site. Repeated clicks return the existing
job. A worker that stops unexpectedly loses its lease; another worker reclaims only unfinished or
expired page items. Completed and HTTP 304 items are never returned to the pending queue. Each page
is retried up to its configured limit before becoming a permanent failure. Writes remain
deterministic and the website source is read-only.

Production cancellation is accepted during page processing. Once the job enters `finalizing`, new
cancel requests are refused so the atomic activation sequence cannot be interrupted. An expired
finalization lease remains recoverable even after the ordinary worker retry limit; the next worker
re-runs idempotent reconciliation and activation instead of falsely failing an already published
snapshot.

### Job execution states

Dashboard derives an operator-facing execution state from the durable job state, lease, retry
deadline, and last progress timestamp:

- `waiting_for_worker`: the job is durable and due, but no worker currently owns its lease;
- `preparing`: a worker is copying or validating the frozen manifest in resumable batches;
- `waiting_retry`: `available_at` is in the future while bounded retry backoff is active;
- `processing`: page checkpoints are actively being leased and processed;
- `recovery_pending`: the previous worker lease expired and another healthy worker may reclaim it;
- `recovery`: a production job exhausted its worker lease retry budget and a worker is performing
  the required idempotent staging cleanup before the job can become terminal;
- `stalled`: no durable progress has been recorded for ten minutes and operations should inspect
  the worker, PostgreSQL, Qdrant, and the source site;
- `finalizing`: all page work is terminal and the worker is performing unsliced publication and
  reconciliation;
- `attention_required`: automatic processing stopped and an explicit retry or staging cleanup is
  required.

Clients use `state_version` to reject out-of-order polling responses. A normal time-slice yield is
not a failure: completed checkpoints remain durable, `available_at` makes the job claimable again,
and a later tenant turn resumes from `prepare_stage` and `prepare_cursor` or the next page item.

## Deployment

For the local Dashboard environment, start the opt-in worker once and leave it running:

```bash
docker compose --profile web-sync up -d api dashboard web-sync-worker
docker compose --profile web-sync ps api web-sync-worker
```

After the worker container exists, its `restart: unless-stopped` policy keeps it available across
Docker restarts. Dashboard operators only queue work; they do not run crawler scripts for each
synchronization. Keep `WEB_CRAWLER_PRODUCTION_SYNC_ENABLED=false` during shadow rollout.

If Dashboard remains at **Waiting for worker 0/N**, the request was accepted but no worker has
claimed it. Check the worker, registry coverage, and dependencies before canceling or creating
another job:

```bash
docker compose --profile web-sync ps -a
docker compose --profile web-sync logs --since 10m web-sync-worker
docker compose --profile web-sync up -d postgres qdrant web-sync-worker
docker compose --profile web-sync ps web-sync-worker
```

Repeated `knowledge dependency is not ready` messages mean the worker is still initializing
Qdrant and has not entered the database claim loop. Restore the failed dependency and recreate the
worker with the current Compose configuration. A prepared or queued job is durable and will be
claimed automatically after the worker becomes healthy; do not create duplicate replacement jobs.
The API admits new jobs from fresh tenant-scoped rows in `web_sync_worker_registrations`. The
worker writes `WEB_SYNC_WORKER_HEARTBEAT_PATH` for Docker container health, and Compose mounts the
same diagnostic file read-only into the API container for compatibility, but that file does not
prove tenant coverage. Missing, expired, unhealthy, draining, or unknown registry coverage fails
closed for that tenant.

Validate Compose, then apply migrations and start the API, Dashboard, data services, and opt-in
worker profile. The one-shot `migrate` service runs `alembic upgrade head` and the structural
knowledge schema contract before the API or worker is allowed to start. A current Alembic revision
is not sufficient by itself: missing indexes, retained legacy unique constraints, or invalid
lifecycle checks fail the migration container closed.

```bash
docker compose --env-file .env.production -f compose.production.yaml --profile web-sync config --quiet
docker compose --env-file .env.production -f compose.production.yaml --profile web-sync up -d --build
docker compose --env-file .env.production -f compose.production.yaml --profile web-sync ps -a
docker compose --env-file .env.production -f compose.production.yaml --profile web-sync logs migrate
```

`knowledge_version_schema_invariant_violation` is terminal. Do not retry affected pages until the
schema contract passes. After the corrective migration is deployed, retry only the failed pages in
the retained snapshot; if retention expired, abandon the staging snapshot and enqueue a fresh
manifest-bound job.

Required production configuration:

```env
WEB_CRAWLER_ENABLED=true
WEB_CRAWLER_PRODUCTION_SYNC_ENABLED=false
WEB_CRAWLER_PREFLIGHT_MAX_SITEMAPS=500
WEB_CRAWLER_SITEMAP_MAX_RESPONSE_BYTES=10000000
WEB_CRAWLER_SITEMAP_MAX_DECOMPRESSED_RESPONSE_BYTES=20000000
WEB_CRAWLER_MANIFEST_MAX_AGE_HOURS=24
WEB_CRAWLER_MAX_PAGES=500
WEB_CRAWLER_MANIFEST_SAFETY_CEILING=50000
WEB_CRAWLER_FULL_SHADOW_ENABLED=true
WEB_CRAWLER_MAX_SITEMAPS=3
WEB_CRAWLER_MAX_RESPONSE_BYTES=2000000
WEB_CRAWLER_MAX_DECOMPRESSED_RESPONSE_BYTES=4000000
WEB_CRAWLER_MAX_COMPRESSION_RATIO=50
WEB_CRAWLER_BATCH_SIZE=250
WEB_SYNC_WORKER_POLL_SECONDS=5
WEB_SYNC_WORKER_CONCURRENCY=4
WEB_SYNC_DOMAIN_CONCURRENCY=2
WEB_SYNC_PREPARE_BATCH_SIZE=500
WEB_SYNC_WORKER_LEASE_SECONDS=120
WEB_SYNC_WORKER_HEARTBEAT_SECONDS=30
WEB_SYNC_WORKER_HEARTBEAT_PATH=/app/web-sync-status/heartbeat
WEB_SYNC_STAGING_RETENTION_HOURS=48
WEB_SYNC_MAX_ACTIVE_TENANTS=4
WEB_SYNC_MAX_PAGES_IN_FLIGHT=8
WEB_SYNC_MAX_PAGES_PER_TENANT=2
WEB_SYNC_MAX_FINALIZATIONS=1
WEB_SYNC_WORK_QUANTUM_PAGES=20
WEB_SYNC_WORK_QUANTUM_SECONDS=30
WEB_SYNC_TENANT_WATCHDOG_SECONDS=1020
WEB_SYNC_MAX_NO_PROGRESS_SECONDS=90
WEB_SYNC_FINALIZATION_TIMEOUT_SECONDS=960
WEB_SYNC_TENANT_DISCOVERY_SECONDS=10
WEB_SYNC_IDLE_BACKOFF_MAX_SECONDS=30
WEB_SYNC_WORKER_REGISTRY_TTL_SECONDS=90
QDRANT_TRUST_ENV=false
```

`WEB_CRAWLER_MAX_PAGES` remains the limit for legacy direct crawler calls. Manifest-bound jobs use
`WEB_CRAWLER_MANIFEST_SAFETY_CEILING`, which is an anomaly fuse rather than a business page limit.
Scopes above the fuse require capacity approval and are never silently truncated.
Sitemap discovery uses its own 10 MB wire and 20 MB decompressed budgets so large XML inventories
do not require increasing the stricter HTML page budget.

The coordinator refreshes the trusted active-tenant catalog every
`WEB_SYNC_TENANT_DISCOVERY_SECONDS` and runs at most `WEB_SYNC_MAX_ACTIVE_TENANTS` tenant turns at
once. Ready tenants are selected round-robin with bounded idle backoff. Each turn stops taking new
work after `WEB_SYNC_WORK_QUANTUM_PAGES` or `WEB_SYNC_WORK_QUANTUM_SECONDS`, whichever is reached
first, then safely yields its durable lease. Finalization is not time-sliced and is separately
bounded by `WEB_SYNC_MAX_FINALIZATIONS`. Tenant turns are tracked independently, so one long
finalization does not hold the other scheduler slots or stop later tenants from rotating in.
`WEB_SYNC_TENANT_WATCHDOG_SECONDS` is the outer task bound; it must remain above
`WEB_SYNC_FINALIZATION_TIMEOUT_SECONDS`. The finalization bound must cover
`QDRANT_MAINTENANCE_TIMEOUT_SECONDS`.

`WEB_SYNC_WORKER_CONCURRENCY` is the legacy per-job ceiling. The effective per-tenant page ceiling
is the lower of that value and `WEB_SYNC_MAX_PAGES_PER_TENANT`; every tenant also shares the
process-wide `WEB_SYNC_MAX_PAGES_IN_FLIGHT` semaphore. Capacity is acquired before a page row is
leased so a page never sits in `fetching` merely while waiting for another tenant to release a
process slot. `WEB_SYNC_DOMAIN_CONCURRENCY` further limits requests to one origin. Size the worker
replica count and database connection pool together so the aggregate connection and embedding
load remain within the PostgreSQL, Qdrant, CPU, and memory budgets.

Task preparation copies `WEB_SYNC_PREPARE_BATCH_SIZE` immutable manifest rows per transaction and
persists its stage and cursor before yielding. Fixed shadow samples use deterministic URL-hash
ordering. Each task records both the generated manifest version and its fingerprint for audit and
replay. Missing product identities and duplicate-product policy decisions use the same bounded,
resumable cursor model. The duplicate policy pages distinct normalized identity keys, locks all
candidates for one key, and persists one authoritative decision before another quantum resumes.

HTTP 429/503 and transient network failures release the page lease and set `next_attempt_at` from
`Retry-After` or exponential backoff. Queued jobs with no due pages are not claimed, preventing the
worker from busy-polling the database while it waits.

With no `--tenant-id` arguments, the worker dynamically discovers every active tenant from the
trusted catalog. A newly activated tenant becomes eligible within two discovery intervals without
restarting or editing Compose. Each concurrent tenant turn enters its own `tenant_scope`; the
worker continues to use the ordinary runtime database role and PostgreSQL row-level security.

`--tenant-id` is a restrictive maintenance filter, not the production tenant directory. It limits
the dynamically discovered set and is useful for a one-turn diagnostic or a controlled canary. It
must not be used to maintain a permanent hand-written list of customer tenants. For example:

```bash
docker compose --env-file .env.production -f compose.production.yaml --profile web-sync run --rm \
  web-sync-worker python scripts/run_web_sync_worker.py --tenant-id tenant-a --once
```

The tenant filter only accepts tenant IDs already returned by the trusted catalog. Browser input,
job payloads, URLs, and model output can never add tenant coverage.

### Registry admission and capacity

The worker writes one expiring registration per covered tenant and worker instance. The API
evaluates three independent facts for the authenticated tenant:

- **Health**: dependency readiness is healthy and the worker is not draining;
- **Coverage**: at least one non-expired registration exists for this tenant;
- **Capacity**: how many covered worker instances are currently idle versus executing a tenant
  turn.

A healthy covered worker may accept another durable job even when all current instances are busy;
capacity is telemetry, not a reason to reject an ordinary queued job. Unknown registry state,
expired coverage, an uncovered tenant, unhealthy dependencies, or draining workers return HTTP
503 with a stable reason code and `Retry-After`. Cancellation and approved cleanup remain durable
operations and must not depend on worker health.

### Safe rollout and recovery

Keep publication disabled for the first multi-tenant rollout. After deployment, confirm the
migration completed, the worker container is healthy, and each canary tenant reports healthy,
fresh, covered registry status before enabling production publication:

```bash
docker compose --env-file .env.production -f compose.production.yaml --profile web-sync ps -a
docker compose --env-file .env.production -f compose.production.yaml --profile web-sync logs --since 10m migrate web-sync-worker api
```

Run concurrent shadow canaries for at least two tenants, including one large and one small
manifest. Verify that the small job starts while the large job is running, total page concurrency
never exceeds the configured global limit, and neither tenant observes the other's jobs or
vectors. Then stop and restart the worker during preparing, processing, and finalizing drills and
verify lease-based recovery before setting `WEB_CRAWLER_PRODUCTION_SYNC_ENABLED=true`.

When PostgreSQL, Qdrant, Docker, or the host restarts, recover the dependency set and Worker with
the same production environment, then inspect health and logs:

```bash
docker compose --env-file .env.production -f compose.production.yaml --profile web-sync \
  up -d postgres qdrant redis api web-sync-worker dashboard
docker compose --env-file .env.production -f compose.production.yaml --profile web-sync ps -a
docker compose --env-file .env.production -f compose.production.yaml --profile web-sync \
  logs --since 10m migrate api web-sync-worker
```

Do not delete or recreate queued jobs during recovery. Due work is reclaimed after registry
coverage and dependency readiness return; expired leases become `recovery_pending`, delayed work
remains `waiting_retry` until `available_at`, and completed page checkpoints are not repeated.
When a production lease exhausts its retry budget, the task enters `cleanup_pending`; a worker must
successfully perform the idempotent staging abort before the database task is allowed to become
terminal. Cleanup failures retain the active-site guard and retry after a bounded delay.

## Scheduled synchronization

Use the deployment scheduler to enqueue an approved manifest rather than running the crawler
inside cron. This keeps scope approval, scheduling, execution, retry, and reporting separate.

Example weekly command:

```bash
docker compose -f compose.production.yaml run --rm --no-deps web-sync-worker \
  python scripts/enqueue_web_sync_job.py \
  --tenant-id tenant-demo \
  --site-id storefront-main \
  --manifest-id MANIFEST_ID \
  --mode shadow \
  --sample-size 500
```

The default idempotency key contains the tenant, site, and current date, so scheduler retries on the
same day do not create duplicate work. A custom `--idempotency-key` can represent a weekly schedule
window when required.

Recommended initial frequency:

- Product catalogs with frequent price or availability changes: daily.
- Mostly static catalogs and policy content: weekly.
- Manual synchronization: after an urgent published correction or before a reviewed campaign.

## Alerts and operating thresholds

Alert operations when any of these conditions is true:

- a queued job has not started within 10 minutes;
- a running job heartbeat is older than the worker lease;
- a job remains in `cleanup_pending` beyond one cleanup retry interval;
- the most recent job failed or did not publish;
- `failed_count > 0`;
- `blocking_issue_count > 0` or `publication_block_reasons` is non-empty;
- `pending_removal_count` or `expired_count` changes unexpectedly;
- `duplicate_product_count` increases above the reviewed baseline;
- no successful snapshot has been published within twice the scheduled interval.

Runbook response:

1. Confirm the worker container is healthy and can reach PostgreSQL, Qdrant, and the public site.
2. Review the job error code and report in Dashboard and the corresponding audit events.
3. Correct configuration or source-site issues.
4. For an ordinary network, crawl, or parser failure, use **Retry failed pages** before retention
   expires. If the report contains `ProductIdentityConflictError`, `unresolved_product_identity`,
   or `duplicate_winner_invariant_violation`, abandon and clean staging, then enqueue a new
   manifest-bound task; failed-page retry intentionally refuses these identity states. Do not
   delete the active snapshot or manually activate staged points.

## Rollout gates

### Gate 1: current production-safe scope

Keep `WEB_CRAWLER_PRODUCTION_SYNC_ENABLED=false`. Complete the 20-page, 500-page, and full-manifest shadow runs,
then verify:

- the manifest contains zero translated-path URLs;
- translated child sitemaps were counted but not requested;
- the second run against the same manifest is dominated by HTTP 304 reuse;
- shadow runs validate parsing, product extraction, chunking, and embeddings without activation;
- duplicate and pending-removal reports match reviewed source behavior.

Shadow completion is recorded as success with `publication_status=not_requested`.

### Gate 2: delivered resumable, fair multi-tenant processing

Migrations `n4c5d6e7f890`, `o5d6e7f8a901`, and `e2f3a4b5c6d7` add the page queue,
blocked-remediation retention, immutable page validator state, recoverable `preparing` phase, and
delayed page retries. The worker resumes the same immutable manifest and policy fingerprint,
reclaims expired leases with `FOR UPDATE SKIP LOCKED`, and reconciles expected, prepared, and
terminal item counts before finalizing. Migrations `b2c3d4e5f6a7` and `c3d4e5f6a7b8` add the
tenant-scoped worker registry, fair claim indexes, resumable preparation cursors, scheduling
counters, `state_version`, progress timestamps, and `available_at`. Migration `d4e5f6a7b8c9`
creates the queue and recovery indexes online; merge revision `e5f6a7b8c9d0` joins that Web Sync
chain with the platform directory chain so production deployment has one Alembic head.
Migration `f7b8c9d0e1f2` adds frozen normalized identities, an authoritative identity-decision
table, normalized product uniqueness, and tenant RLS. It requires online Alembic execution because
the Unicode NFKC/casefold backfill cannot be reproduced by PostgreSQL `lower()`.

The resumable design must distinguish:

- process interruption: keep staging data and resume unfinished URLs;
- deterministic production page failure: retain staging and block publication;
- policy or sitemap scope change: abandon the old staging snapshot and start a new one;
- operator remediation retry: reset only failed pages in the same job and snapshot ID; deterministic
  exclusions remain terminal and auditable;
- product-identity remediation: reject page-only retry, clean the retained staging snapshot, and
  create a new task so authoritative decisions run before any page writes staging data;
- finalization-only failure: retain all staging artifacts and mark the job blocked with a
  `finalization` report error; operator retry keeps every page terminal and reruns only strict
  PostgreSQL/Qdrant/product reconciliation and activation;
- Qdrant publication and snapshot cleanup use `QDRANT_MAINTENANCE_TIMEOUT_SECONDS` rather than the
  short query timeout because a large site-wide payload transition can legitimately exceed one
  minute; the default is 900 seconds and the accepted range is 60 to 3600 seconds;
- operator abandon or retention expiry: discard staged PostgreSQL versions, Qdrant points, and
  product facts while leaving the active snapshot unchanged;
- successful production completion: atomically replace the site's validators in
  `web_crawl_page_states`, pruning excluded or removed URLs without mutating the frozen manifest;
- successful shadow completion: aggregate a non-publishing report without changing live knowledge.

### Gate 3: delivered production publication pipeline

The code-level production capability is enabled after staged page writes, PostgreSQL/Qdrant/product
reconciliation, compensating knowledge rollback, finalization lease recovery, and tenant-scoped
integration tests were delivered. The deployment flag remains off by default, so a formal job is
still unavailable to operators until rollout validation is complete.

Before activation, the worker requires:

- every frozen manifest item to be in a terminal state;
- zero permanent page failures and no crawl truncation;
- zero unresolved duplicate product identities;
- the summed changed-page chunk count to equal indexed PostgreSQL chunk manifests;
- the same chunk count to equal Qdrant points carrying the production snapshot ID;
- the number of unique page product keys to equal staged product rows;
- all activation references to remain within the trusted tenant and site.

The preflight may read the multilingual root sitemap index, but it must not fetch translated child
sitemaps. Production jobs consume only the frozen main-language manifest.

### Gate 4: operator enablement

Keep `WEB_CRAWLER_PRODUCTION_SYNC_ENABLED=false` until the 20-page, 500-page, and full-manifest
shadow runs, their incremental reruns, and a worker interruption/resume drill pass in the target
environment. Then set the flag to `true`, restart the API and worker, run a fresh preflight, and
queue the first formal job from Dashboard. Do not raise the safety ceiling without a capacity
review.

## Acceptance checklist

- Migrations through current Alembic head `e5f6a7b8c9d0` are applied by the dedicated migration
  role without disabling forced RLS.
- Dashboard users with `knowledge:read` can view jobs.
- Only users with `knowledge:sync` can run preflight or queue jobs.
- Jobs cannot be queued without a ready manifest from the same tenant and site.
- A translated sitemap or first-path locale never enters the manifest.
- Completed pages survive worker termination and only unfinished pages are reclaimed.
- Page retries are bounded and permanent failures are visible by URL.
- Queued and running jobs support safe cancellation; blocked jobs support failed-page retry,
  zero-refetch finalization retry, and explicit staged-data cleanup.
- Production code readiness is enabled, while the deployment publication flag remains off until
  the target-environment rollout gates pass.
- Production finalization reconciles PostgreSQL chunks, Qdrant snapshot points, and unique staged
  products before activation.
- Product activation failure restores the previous PostgreSQL and Qdrant version sets.
- Production cancellation cleans staging data during processing and is refused during finalizing.
- The worker profile is running when crawling is enabled.
- A newly activated tenant receives fresh registry coverage within two discovery intervals without
  a worker restart or a Compose command change.
- Worker health for tenant A never makes enqueue available to an uncovered tenant B.
- A large tenant and a small tenant run concurrently without exceeding global, per-tenant, domain,
  or finalization limits; the small job receives a turn within the reviewed fairness threshold.
- Two worker replicas competing for the same tenant never process one job lease concurrently.
- Two clicks for the same active site return one job.
- API requests finish immediately with HTTP 202.
- Worker termination leaves the previous snapshot serving and the job becomes reclaimable.
- Failed or truncated crawls never publish a partial snapshot.
- Scheduler execution and manual execution are visible in the same history.
- PostgreSQL, Qdrant, RLS, Ruff, formatting, Python tests, and Dashboard build pass before release.
