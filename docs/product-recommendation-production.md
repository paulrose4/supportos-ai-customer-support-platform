# Product Recommendation Production Runbook

The recommendation path is tenant and site scoped. It returns only canonical product URLs from
the active PostgreSQL catalog snapshot. Models can improve ranking but cannot add a product,
override a hard constraint, or emit a link.

## Enablement Order

1. Deploy with `PRODUCT_RECOMMENDATION_ENABLED=true`, but leave the index, reranker, planner,
   and reviewer disabled. Verify catalog snapshots include price, currency, stock, material,
   dimensions, and shipping regions.
2. Enable `PRODUCT_RECOMMENDATION_INDEX_ENABLED=true`. Run a staged site synchronization to
   populate `PRODUCT_RECOMMENDATION_COLLECTION`; the active snapshot then refreshes its product
   vectors. Verify query latency and `product_recommendation_index_*` warnings.
3. Enable the BGE reranker. Keep `PRODUCT_RECOMMENDATION_RERANKER_PREWARM=true` so a bad model
   download or runtime configuration fails at deployment rather than under customer traffic.
4. Run planner and reviewer in shadow mode operationally before enabling their flags. Their
   structured outputs must be logged, compared against deterministic results, and checked for
   evidence-field validation failures.
5. Enable the planner before the reviewer. Keep LLM review limited to the top eight candidates.

## Required Checks

- A product catalog sync must complete after the index feature is enabled.
- The `fastembed_cache` Docker volume must be writable by the `app` user and retained across
  deploys. It contains downloaded local embedding and reranker models.
- The recommendation Qdrant collection must be distinct from the knowledge collection and use
  the same vector dimension as the configured embedding provider.
- Test exact requirements for currency, price, stock, shipping region, material, height, and
  weight before enabling a site.
- Treat `cross_encoder_unavailable`, `product_recommendation_index_failed`, and
  `candidate_review_evidence_rejected` as operational alerts. They degrade to deterministic
  ranking and never relax hard constraints.

## Customer Output Contract

Clients should render `recommended_products` as product cards. `source_url` is the approved
canonical link. `related_links` remains for compatibility, but clients must not derive product
links from assistant prose.
