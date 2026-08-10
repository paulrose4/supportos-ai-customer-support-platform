# Memory V2 and Retrieval Plan

The production support path keeps customer memory in PostgreSQL and knowledge retrieval in
Qdrant. Customer memory is never projected into the knowledge collection.

## Rollout switches

The following settings are independently switchable and default to enabled in development:

- `RETRIEVAL_PLAN_ENABLED`
- `RETRIEVAL_QUERY_ENHANCEMENT_ENABLED`
- `RETRIEVAL_RELATION_EXPANSION_ENABLED`
- `MEMORY_CANDIDATE_WORKFLOW_ENABLED`

Disable query enhancement or relation expansion without changing the Qdrant collection. A change
to the dense model or embedding dimension still requires a new collection and index namespace.

## Memory lifecycle

Customer memory follows this state machine:

`conversation fact -> candidate -> consent/review -> active memory -> expired/superseded/deleted`

Candidates are not durable memory. They are bounded, tenant-scoped, audited records. LLM output can
propose a candidate in a future worker, but it cannot call the durable-memory write port. Sensitive
regulated-product preferences require explicit consent. Orders, payments, addresses, identity claims,
health claims, passwords, and instruction-like text are not durable memory.

Only memories selected for a completed grounded answer update `last_used_at` and `use_count`.
The update rechecks tenant, customer, consent, active status, and expiry in PostgreSQL and emits a
`customer_memory.used` audit event; model output cannot choose the memory IDs being updated.

The default TTLs are 90 days for preferences and resolutions, 180 days for verified products, and
30 days for troubleshooting records. The privacy owner must approve these values before production.

Resolution episodes are created only after customer confirmation, approved human resolution, or
verified inactivity resolution. They describe the previous support action; they never replace a
fresh order, inventory, price, or logistics lookup.

## Retrieval contract

`RetrievalPlan` determines intent, entities, sources, trusted filters, query variants, top-k,
reranking mode, latency budget, and fallback policy. Exact SKU and URL questions use one exact
query. Transactional and policy questions use strict same-language evidence and do not use HyDE.
Query expansion is limited to non-transactional ambiguous questions and at most three variants.

The query contains the current user question and structured conversation context. Assistant prose is
not used as retrieval evidence. Dense and sparse embedding failures degrade independently to
dense-only or sparse-only retrieval; both failing is a handoff condition.

## Knowledge indexing

Chunking is deterministic and category-aware. Delivery chunks keep processing time, transport time,
warehouse and coverage rules together. Product, policy, care and general sections have separate
chunk types and bounded parent context. Changing the chunker requires a new index namespace and a
Recall@10 comparison before activation.

Existing `knowledge_links` provide a one-hop relation expansion for product-to-policy and
product-to-care retrieval. Neo4j is intentionally out of scope until real multi-hop failures and
catalog scale justify it.

## Release evidence

Before enabling the workflow for customers, collect:

- 300 multi-turn reference and structured-product cases;
- 100–300 multilingual retrieval cases with expected document IDs;
- 100 memory expiry, conflict, consent and prompt-injection cases;
- 100 resolution-episode reuse cases;
- route-level P50/P95/P99 and degraded retrieval counts.

The release gate remains fail-closed at Recall@10 >= 0.92, no cross-tenant leakage, no unconsented
memory writes, no expired-memory usage, multi-turn reference accuracy >= 0.98, and first visible
response P95 below three seconds.
