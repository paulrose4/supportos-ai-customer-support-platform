# Website Product Duplicate Policy

Website synchronization treats a non-placeholder SKU or MPN as the product identity within one tenant, site, and sync job. Identity is normalized with Unicode NFKC, case folding, and whitespace removal; meaningful `-` and `/` characters are preserved. Placeholder values such as `N/A`, `unknown`, `default`, and `0` do not participate in SKU grouping.

The global production defaults are:

```dotenv
DUPLICATE_PRODUCT_POLICY=first_wins
DUPLICATE_PRODUCT_ORDER=manifest_ordinal
```

`first_wins` is decided during the immutable manifest preparation phase, before a Worker can stage a page. The manifest freezes `normalized_product_key` and `normalization_version=product-identity-v1`; raw SKU/MPN values remain available for display and audit. Existing page states provide identities immediately; for new or incomplete product pages, preparation performs a bounded identity-only fetch that extracts SKU/MPN without indexing.

Preparation pages distinct normalized identities rather than ordinal windows, then loads every candidate for one identity as a group. The winner is the smallest manifest ordinal, unless a still-valid winner URL from the latest successful publication with the same normalization version is present in the new manifest. The authoritative result is stored once in `web_sync_product_identity_decisions`, uniquely keyed by tenant, job, and normalized identity. `web_sync_job_items.winner_item_id` and `winner_url` are compatibility projections and must agree with that decision. Duplicate items remain auditable with `status=excluded`, `outcome_reason=duplicate_product_first_wins`, `identity_source`, and `policy_version=duplicate-product-v2`. They do not create knowledge documents, chunks, embeddings, product snapshots, recommendation records, or Qdrant points.

If the winner reaches a terminal fetch/exclusion failure, the Worker locks the decision row, promotes the next candidate in manifest order, increments `decision_revision`, and rewrites every item projection in the same transaction. This is transactional and idempotent, so a Worker restart cannot create two winners. A site may override the global policy with `block` or `manual_review` through the nullable `support_sites.duplicate_product_policy` column; those modes leave duplicates unresolved and block production publication until reviewed.

Production finalization fails closed when a normalized identity has no decision, a decision is orphaned, item projections disagree, normalization versions differ, or a non-winner is marked successful. It also reconciles PostgreSQL chunks, Qdrant points, and staged product counts before publication. Product facts have a normalized-identity unique constraint so raw case or Unicode spelling cannot create a second product in one snapshot.

## Rollout

1. Deploy the code with `WEB_CRAWLER_PRODUCTION_SYNC_ENABLED=false`.
2. Run `alembic upgrade head` in online mode. Migration `f7b8c9d0e1f2` performs Python NFKC/casefold backfill and intentionally rejects Alembic `--sql`; PostgreSQL `lower()` is not equivalent. Resolve any reported placeholder identities or normalized product collisions before retrying the migration.
3. Confirm the effective policy in the queued job snapshot. Existing jobs retain their saved policy; create a new immutable manifest for a new run.
4. Run shadow jobs first. Review `duplicate_product_total`, `duplicate_product_excluded_count`, `duplicate_product_unresolved_count`, and `winner_product_count`.
5. Verify excluded duplicate items have no active knowledge version, product snapshot, or Qdrant point. Production publication is allowed when unresolved duplicates and permanent failures are zero and PostgreSQL/Qdrant reconciliation passes.

Do not use **Retry failed pages** for a blocked task containing `ProductIdentityConflictError`, `unresolved_product_identity`, or `duplicate_winner_invariant_violation`. Abandon its retained staging snapshot and enqueue a fresh job from a frozen manifest after the code and migration are deployed. Failed-page retry remains valid for ordinary network, crawl, and parser failures; finalization retry remains valid only when page and identity invariants are already clean.

The report fields are additive. `duplicate_product_count` remains for compatibility and represents unresolved duplicate groups. `failed_count` counts failed pages only; `blocking_issue_count` and `publication_block_reasons` describe publication gates. Dashboard checked-page progress uses `completed_count / expected_count`, while `document_count` is shown separately as generated documents.
