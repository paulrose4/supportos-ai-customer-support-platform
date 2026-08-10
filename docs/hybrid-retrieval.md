# Multi-Tenant Hybrid Knowledge Retrieval

## Goal

Every WordPress tenant searches two controlled knowledge partitions:

1. reviewed company-wide knowledge in `__global__`;
2. knowledge owned by the authenticated tenant.

No query may search another tenant partition. PostgreSQL remains authoritative for document versions and synchronization state; Qdrant remains a rebuildable retrieval projection.

## Qdrant Schema

The default collection is `customer_support_knowledge_v1`. Every point stores:

- named Dense vector `dense`;
- named Sparse vector `sparse`;
- tenant-indexed `partition_id`;
- `knowledge_scope` equal to `tenant` or `global`;
- publication, audience, language, product, region, authority, priority, source, and effective-date payloads;
- bounded chunk text and deterministic document/version/chunk identifiers.

Tenant knowledge uses `partition_id=<trusted tenant_id>`. Shared company knowledge uses `partition_id=__global__` and `tenant_id=__global__`.

## Query Pipeline

For one authenticated tenant, the adapter performs four candidate searches:

1. tenant Dense;
2. tenant Sparse;
3. global Dense;
4. global Sparse.

Qdrant RRF fuses Dense and Sparse results inside each partition. Application-owned rank fusion then applies the configured tenant/global weights. The default weights are 0.65 and 0.35. A deterministic reranker combines retrieval rank and lexical coverage, and only then applies bounded authority, priority, and scope boosts.

Authority cannot make unrelated evidence eligible. Candidates below `KnowledgeQuery.score_threshold` are discarded. Empty results, conflicts, provider failure, or tenant-invariant failure route to human handoff.

## Index Migration

`KNOWLEDGE_INDEX_SCHEMA_VERSION` identifies the projection format. Changing the collection name or schema version forces existing content to be reprojected even when the Markdown content hash has not changed. The immutable PostgreSQL content version is reused; only its index namespace and active Qdrant point projection change.

Do not mutate the legacy single-vector collection in place. Deploy with a new collection name, synchronize global and tenant knowledge, verify retrieval, and only then retire the old collection.

## Synchronization

Synchronize tenant knowledge:

```powershell
curl.exe --noproxy "*" -X POST http://localhost:8000/v1/knowledge/sync
```

Synchronize reviewed shared company knowledge:

```powershell
curl.exe --noproxy "*" -X POST http://localhost:8000/v1/knowledge/sync/global
```

The global endpoint requires `knowledge:sync:global`. A WordPress site key never receives this scope.

## Current Baseline and Next Adapter

The current local baseline uses the deterministic `FakeEmbeddingProvider`, a hashing Sparse provider, Qdrant RRF, and a deterministic lexical reranker. This makes the full storage, filtering, fusion, migration, and safety behavior runnable without a model credential.

Before production relevance sign-off, replace the Dense and Sparse providers and reranker with evaluated multilingual models. The domain and application layers remain unchanged because these components implement ports.

The future WordPress crawler must produce the same `KnowledgeChunk` projection after domain allow-list validation, SSRF protection, canonical URL handling, main-content extraction, structured WooCommerce extraction, content hashing, deduplication, and publication review. Raw HTML, scripts, navigation, cookie banners, and untrusted page instructions must not be embedded directly.

## Verification

Run all local tests:

```powershell
python -m ruff check .
python -m ruff format --check .
python -m pytest
```

Run real PostgreSQL and Qdrant integration tests:

```powershell
$env:RUN_INTEGRATION_TESTS="1"
$env:TEST_DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/customer_agent"
$env:TEST_QDRANT_URL="http://localhost:6333"
python -m pytest tests/integration -q
```
