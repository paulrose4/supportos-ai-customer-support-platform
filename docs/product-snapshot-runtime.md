# Product Snapshot Runtime

## Runtime scope

The customer-facing agent supports pre-sales product guidance, FAQ answers, product care,
and published shipping, payment, returns, and warranty policies. It does not connect to a
commerce API and cannot query or mutate orders. Order status, refund requests, address changes,
damaged or missing deliveries, fraud, privacy, legal complaints, and other high-risk requests
always create a human handoff.

## Source responsibilities

- PostgreSQL is authoritative for product identity and structured product facts: SKU, MPN,
  canonical URL, name, brand, material, dimensions, weight, page price, page availability,
  warehouse, shipping regions, fetch time, validators, and content hash.
- Recommendation and comparison candidates are selected and freshness-filtered from PostgreSQL;
  the language model may explain or compare those candidates but cannot invent another SKU.
- Qdrant contains explanatory text: product descriptions, FAQ, care and buying guides, policies,
  and brand or material explanations. Its product payload contains identity fields only so it
  can filter an already-identified product; it is not allowed to choose a similar product when
  an exact product reference was supplied.

## Complete publication

One website sync creates a staging product snapshot and inactive Qdrant points. Product rows are
written in batches of `WEB_CRAWLER_BATCH_SIZE` (default 250). The new PostgreSQL snapshot and the
selected Qdrant versions become active only after crawling, quality checks, embedding, and all
batch writes succeed. A failed run discards its inactive points and keeps the previous complete
snapshot active.

The production example permits 5,000 pages, while each PostgreSQL product write remains bounded
to 250 rows. Lower `WEB_CRAWLER_MAX_PAGES` for smaller sites or constrained workers.

Products absent from one successful crawl are carried into the new snapshot as
`pending_removal`. A second consecutive successful crawl that still cannot find them changes
their state to `expired`. This prevents a temporary fetch failure from immediately removing a
product.

## Freshness and answer wording

- Product specifications: usable for 30 days by default.
- Page price: usable for 7 days and always includes the synchronization date and product link.
- Page availability: historical only, usable for 7 days by default, and never described as
  real-time or guaranteed inventory.
- Published policies: the latest successfully activated version remains available. A failed
  sync must alert operations but does not replace it.
- Promotion codes: never confirmed as currently valid; customers are sent to checkout or human
  support.
- Delivery timing: only published ranges may be quoted and no arrival guarantee is made.

Outside a freshness window, the agent omits stale numeric facts and provides the product page or
a human handoff.

## Weekly job

Run migrations first, keep the durable worker active, then schedule an enqueue command weekly:

```powershell
python -m alembic upgrade head
python scripts/enqueue_web_sync_job.py `
  --tenant-id tenant-demo `
  --site-id storefront-main
```

The enqueue command exits after PostgreSQL accepts the durable job. `scripts/run_web_sync_worker.py`
performs the crawl and stores the final report. Dashboard shows discovered, changed, unchanged,
HTTP 304, duplicate, excluded, failed, pending-removal, expired, product, and indexed-chunk counts.
The previous snapshot continues serving until the worker publishes a complete replacement.

When an active page has an `ETag` or `Last-Modified` value, the next sitemap-based crawl sends
`If-None-Match` and `If-Modified-Since`. An HTTP `304` reuses the active Qdrant version and copies
the validated product into the new PostgreSQL snapshot without downloading, parsing, chunking, or
embedding the page again. Crawls that depend only on following page links continue to use full GET
requests so a cached parent page cannot hide newly added child links.

## Real infrastructure tests

Run all PostgreSQL, Qdrant, and restricted-role RLS tests against an isolated disposable database:

```powershell
.\scripts\run_integration_tests.ps1
```

The script starts the existing Docker Compose dependencies, recreates only the named integration
database, applies the full Alembic chain, creates a non-superuser RLS test role, bypasses local HTTP
proxies for Qdrant, and enables `RUN_INTEGRATION_TESTS=1`. It does not modify the development
database configured in `.env`.
