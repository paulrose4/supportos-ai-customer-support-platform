# Website Knowledge Crawler

## Purpose

The website knowledge pipeline turns a registered tenant site's public HTML into normalized, tenant-scoped knowledge chunks. It is read-only toward the source website and is disabled by default. It does not use an LLM for crawling, HTML filtering, product extraction, authorization, deduplication, or indexing decisions.

## Pipeline

1. Resolve the authenticated administrator and require `knowledge:sync`.
2. Resolve `site_id` through the tenant-scoped PostgreSQL site registry; request bodies cannot supply a crawl URL or tenant identity.
3. Read the root sitemap index once, classify GTranslate locale suffixes, and fetch only
   unsuffixed or primary-language child sitemaps.
4. Freeze the filtered primary-language URL set into a tenant/site-scoped immutable manifest.
5. Consume only manifest URLs with internal-link expansion disabled.
6. Respect `robots.txt` and bounded request limits.
7. Reject translated first-path routes before HTTP, including redirect targets.
8. Parse HTML with Selectolax, validate `lang` and canonical language scope, and extract deterministic product facts.
9. Normalize, quality-check, deduplicate, chunk, and validate dense/sparse embeddings.
10. Persist each shadow page result independently and reconcile the frozen expected count before
    marking the job complete.

When an active website version has an `ETag` or `Last-Modified` validator, the next crawl sends
`If-None-Match` or `If-Modified-Since`. HTTP 304 responses reuse the active Qdrant version and copy
the product fact into the new PostgreSQL snapshot. A downloaded page whose explanatory content is
unchanged is also reused through the independent semantic content hash, so price-only changes update
PostgreSQL without regenerating vectors.

## Structured HTML Filtering

The parser discards executable, interactive, layout-only, hidden, and common boilerplate regions, including `script`, `style`, `noscript`, `template`, `svg`, `canvas`, `iframe`, interactive form controls such as inputs and buttons, navigation, footers, sidebars, hidden/`aria-hidden` elements, cookie banners, popups, newsletters, breadcrumbs, social sharing, advertisements, and related-content widgets. It still parses public explanatory text inside a form container because storefront product and shipping details are sometimes nested there.

The normalized document retains only useful knowledge signals:

- title, description, language, canonical URL, and headings;
- paragraphs, lists, specification tables, and same-site links;
- JSON-LD product name, SKU, MPN, brand, category, material, offers, price, currency, availability, images, and additional properties;
- deterministic source, tenant, site, product, authority, priority, version, and content-hash metadata.

Raw HTML is never stored in the domain document or Qdrant payload. Exact duplicate blocks, copyright text, cookie prompts, newsletter prompts, and other known boilerplate phrases are removed before chunking.

## Safety Boundaries

- Crawling performs bounded `GET` requests only; it never submits forms, authenticates to the website, writes files on the website, or invokes commerce actions.
- `tenant_id` comes from the trusted administrator principal.
- `base_url` comes from the tenant-scoped PostgreSQL site record.
- A caller cannot submit an arbitrary URL, redirect host, credential, private IP, localhost address, or non-HTML asset for indexing.
- Manifest, job, and URL identities use tenant/site composite keys and PostgreSQL row-level security.
- Every manifest URL becomes one `web_sync_job_items` row. Page leases and bounded retries allow a
  replacement worker to continue without fetching completed URLs again.
- `/de/product.html` is excluded while `/design/product.html` remains valid because only the first
  path segment is compared with detected locales.
- Qdrant contains knowledge chunks and retrieval metadata only. Site registration, sync versions, manifests, status, errors, and audit/control data remain in PostgreSQL.
- Crawling failures are reported and never converted into invented content. Downstream answer generation must fail closed and hand off when evidence is insufficient or unavailable.

## Write Semantics

| Operation | Idempotency | Permission | Audit/control evidence | Transaction | Human approval |
|---|---|---|---|---|---|
| Website crawl | Read-only; repeated fetches do not mutate the source | Trusted registered active site | Per-run errors and counts | None | Explicit administrator trigger |
| Sync job | Durable idempotent queue; one active job per site | `knowledge:sync` plus tenant-scoped `sites:read` | PostgreSQL status, lease, report, and audit events | Adapter-controlled transactions | Triggering administrator or configured scheduler |
| Page checkpoint | Unique tenant/job/URL item; terminal writes require the current item lease | Inherited worker identity | Per-page status, attempts, duration, validator, and sanitized error | PostgreSQL row transaction | Inherited from sync trigger |
| Version staging | Deterministic version ID from document ID and content hash | Trusted sync service | PostgreSQL version and chunk manifest | PostgreSQL transaction | Inherited from sync trigger |
| Unchanged document | Same active content hash is skipped | Trusted sync service | Existing active manifest | No write required | Inherited from sync trigger |
| Qdrant projection | Deterministic chunk point IDs make retries converge | Trusted index adapter | PostgreSQL point manifest and status | Qdrant operation; not atomic with PostgreSQL | Inherited from sync trigger |
| Changed document replacement | Upsert inactive version, then activate complete site version set | Trusted index adapter | Version/error status in PostgreSQL | Compensating activation on failure | Inherited from sync trigger |

Changed website documents are written as inactive Qdrant points. The service activates the new
site version set only after every crawl, quality, embedding, and product-snapshot step succeeds.
Permanent page failures move a production job to `blocked/awaiting_remediation`. The worker keeps
the successful page checkpoints, PostgreSQL staged versions, Qdrant staged points, and product
snapshot for the configured retention window. An operator can retry only the failed pages in the
same job and snapshot, or abandon the job and clean all staged data. The previous complete version
set remains available throughout remediation.
If the crawler reaches `max_pages` while URLs remain queued, the run is marked truncated and the
staged snapshot is refused. This prevents a page limit from publishing a partial site as if it were
complete.

## Configuration

The crawler is disabled until explicitly enabled:

```env
WEB_CRAWLER_ENABLED=false
WEB_CRAWLER_PRODUCTION_SYNC_ENABLED=false
WEB_CRAWLER_PREFLIGHT_MAX_SITEMAPS=500
WEB_CRAWLER_SITEMAP_MAX_RESPONSE_BYTES=10000000
WEB_CRAWLER_SITEMAP_MAX_DECOMPRESSED_RESPONSE_BYTES=20000000
WEB_CRAWLER_MANIFEST_MAX_AGE_HOURS=24
WEB_CRAWLER_MAX_PAGES=5000
WEB_CRAWLER_MANIFEST_SAFETY_CEILING=50000
WEB_CRAWLER_FULL_SHADOW_ENABLED=true
WEB_CRAWLER_MAX_SITEMAPS=10
WEB_CRAWLER_MAX_RESPONSE_BYTES=2000000
WEB_CRAWLER_MAX_DECOMPRESSED_RESPONSE_BYTES=4000000
WEB_CRAWLER_MAX_COMPRESSION_RATIO=50
WEB_CRAWLER_REQUEST_TIMEOUT_SECONDS=15
WEB_CRAWLER_DELAY_SECONDS=0.25
WEB_CRAWLER_FOLLOW_INTERNAL_LINKS=true
WEB_CRAWLER_RESPECT_ROBOTS_TXT=true
WEB_CRAWLER_BATCH_SIZE=250
WEB_SYNC_WORKER_CONCURRENCY=4
WEB_SYNC_DOMAIN_CONCURRENCY=2
WEB_SYNC_PREPARE_BATCH_SIZE=500
WEB_SYNC_STAGING_RETENTION_HOURS=48
```

The sitemap-specific limits allow large sitemap indexes and product maps without weakening the
smaller per-page fetch budget. Both paths still apply the shared compression-ratio limit while
decoding gzip content.

`QDRANT_TRUST_ENV=false` is the recommended default. It prevents local or private Qdrant traffic
from being routed through workstation `HTTP_PROXY`/`HTTPS_PROXY` settings. Set it to `true` only
when the configured Qdrant endpoint intentionally requires the system proxy.

## Shadow Runs

Run a preflight in Dashboard, then select a fixed 20, 100, 200, or 500 page sample, or the complete
immutable manifest. Fixed samples use a stable URL hash order so they remain reproducible without
being biased toward the first alphabetic URLs. Every manifest stores a generated version, fingerprint,
URL count, and content-kind counts without requiring the Dashboard to reload every manifest item.
Full shadow validates every frozen URL but never activates PostgreSQL or Qdrant
snapshots. Production completion persists ETag and Last-Modified validators in the separate
`web_crawl_page_states` table; manifests are never modified after creation. Later manifests copy a
point-in-time validator snapshot and can issue conditional requests.
Worker termination keeps terminal page items intact. A later worker claims only pending or expired
items. Operators can safely cancel a queued or running job; the current page is allowed to finish,
then remaining items are marked canceled.

```powershell
python scripts/enqueue_web_sync_job.py `
  --tenant-id tenant-demo `
  --site-id storefront-main `
  --manifest-id MANIFEST_ID `
  --mode shadow `
  --sample-size 200
```

The JSON report distinguishes:

- `unchanged_document_count`: all documents that reused an active semantic version;
- `http_not_modified_count`: the subset reused directly from HTTP 304 responses;
- `duplicate_product_count`: repeated SKU/MPN identities observed across the batch;
- `indexed_chunk_count`: chunks whose dense and sparse embeddings were validated; shadow mode does
  not write or activate them.

The legacy direct API endpoint is blocked. Do not bypass manifest validation for operator runs.
Formal publication uses the same immutable manifest and page checkpoints, then reconciles indexed
PostgreSQL chunk manifests, Qdrant points for the job snapshot ID, and unique staged products before
activation. The implementation capability is ready, but the deployment flag remains `false` by
default until the 20-page, 500-page, and full-manifest target-environment rollout gates pass.

After approval, queue a complete production manifest without a sample size:

```powershell
python scripts/enqueue_web_sync_job.py `
  --tenant-id tenant-demo `
  --site-id storefront-main `
  --manifest-id MANIFEST_ID `
  --mode production
```

During page processing, safe cancellation cleans the current staging snapshot and leaves the old
snapshot serving. A blocked job exposes `retry` and `abandon and clean staging` actions in Dashboard;
blocked staging expires automatically after `WEB_SYNC_STAGING_RETENTION_HOURS`. Cancellation is
refused once the job enters `finalizing`; an expired finalization lease is reclaimed until the
idempotent activation and task record are complete.


## Local Test Profile

The current development `.env` uses a conservative profile to limit host resource usage:

```env
WEB_CRAWLER_ENABLED=false
WEB_CRAWLER_MAX_PAGES=500
WEB_CRAWLER_MAX_SITEMAPS=3
WEB_CRAWLER_MAX_RESPONSE_BYTES=2000000
WEB_CRAWLER_MAX_DECOMPRESSED_RESPONSE_BYTES=4000000
WEB_CRAWLER_MAX_COMPRESSION_RATIO=50
WEB_CRAWLER_DELAY_SECONDS=0.5
WEB_SYNC_STAGING_RETENTION_HOURS=48
```

Enable the crawler only for the manual test window, run one synchronization, inspect the report and Docker resource usage, then disable it again. These limits bound one crawl; PostgreSQL version/audit history and Qdrant payloads still require periodic disk monitoring.

After a trusted site has been registered, an administrator first runs:

`POST /v1/knowledge/web-crawl-preflights/{site_id}`

Then a shadow validation can queue:

`POST /v1/knowledge/web-sync-jobs/{site_id}`

The request returns HTTP 202 after the job is stored. A separate
`scripts/run_web_sync_worker.py` process performs the long-running crawl. See
`docs/web-knowledge-operations.md` for deployment, scheduling, alerts, and rollout gates.

Do not enable this against a production site until crawl limits, robots policy, site ownership, and expected URL scope have been reviewed.
