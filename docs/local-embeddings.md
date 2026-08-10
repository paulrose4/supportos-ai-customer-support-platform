# Local Embeddings

## Decision

The application uses the FastEmbed ONNX runtime for local dense embeddings while the chat model remains an OpenAI-compatible remote adapter. The initial model is `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, producing 384-dimensional vectors and supporting multilingual customer-support and website content.

The selected model is intentionally smaller than multilingual MPNet, E5-large, and Jina v3 because this host has 16 GB RAM and also runs PostgreSQL, Qdrant, the API, Dashboard, and local development workloads. The model can be upgraded behind the same `EmbeddingProviderPort` after retrieval evaluations justify the additional memory and latency.

## Runtime Boundary

- Domain and application layers depend only on `EmbeddingProviderPort`.
- `FastEmbedEmbeddingProvider` lives under `app/integrations/llm` and owns the FastEmbed SDK.
- Embedding work runs in a worker thread so ONNX inference does not block the FastAPI event loop.
- The adapter forces the CPU execution provider, validates vector count, dimension, and finite values, and fails closed on model/runtime errors.
- FastEmbed is warmed during application startup, so readiness is not published until the local model can generate a valid vector.
- Host model files are cached under `.cache/fastembed` and excluded from version control. Docker uses the persistent `fastembed_cache` volume mounted at the image-owned `/home/app/.cache/fastembed`, so Linux cache metadata is isolated from the Windows bind mount and remains writable by the non-root `app` user. No customer text is persisted in the model cache.

## Qdrant Migration

Fake embeddings used 64 dimensions. Local FastEmbed vectors use 384 dimensions, so the application writes to a new collection:

```env
EMBEDDING_PROVIDER=fastembed
EMBEDDING_DIMENSION=384
QDRANT_COLLECTION=customer_support_knowledge_fastembed_v1
KNOWLEDGE_INDEX_SCHEMA_VERSION=hybrid-fastembed-minilm-v1
LOCAL_EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
LOCAL_EMBEDDING_CACHE_DIR=./.cache/fastembed
LOCAL_EMBEDDING_THREADS=4
LOCAL_EMBEDDING_BATCH_SIZE=32
```

The old 64-dimensional collection is retained for rollback. Knowledge must be synchronized again because vectors from different models or dimensions cannot be mixed. The changed index namespace forces PostgreSQL control-plane records to create a fresh projection rather than incorrectly skipping previously indexed content.

## Write Semantics

| Write | Idempotency | Permission | Audit/control evidence | Transaction | Human approval |
|---|---|---|---|---|---|
| Model cache download | Content-addressed provider cache; retries converge | Host process filesystem permission | Runtime logs only; no customer content | Atomicity controlled by FastEmbed cache | Operator enables provider |
| Qdrant collection creation | Create-if-missing by fixed collection name | Infrastructure adapter | Readiness status and collection metadata | Qdrant operation | Configuration review |
| Knowledge reindex | Deterministic version/chunk IDs and new namespace | Existing `knowledge:sync` controls | PostgreSQL manifests and sync reports | PostgreSQL/Qdrant are not cross-store atomic | Explicit administrator sync |

A model name or dimension change requires a new Qdrant collection and schema version. In-place mutation is prohibited.
