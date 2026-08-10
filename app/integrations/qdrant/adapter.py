import asyncio
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from qdrant_client import AsyncQdrantClient, models

from app.domain.models import KnowledgeEvidence
from app.domain.ports import (
    GLOBAL_KNOWLEDGE_PARTITION,
    EmbeddingProviderPort,
    KnowledgeChunk,
    KnowledgeIndexSiteAudit,
    KnowledgeQuery,
    KnowledgeRerankerPort,
    SparseEmbeddingProviderPort,
)

_DENSE_VECTOR = "dense"
_SPARSE_VECTOR = "sparse"
_PAYLOAD_INDEXES: dict[str, models.PayloadSchemaType | models.KeywordIndexParams] = {
    "partition_id": models.KeywordIndexParams(
        type=models.KeywordIndexType.KEYWORD,
        is_tenant=True,
    ),
    "knowledge_scope": models.PayloadSchemaType.KEYWORD,
    "tenant_id": models.PayloadSchemaType.KEYWORD,
    "site_id": models.PayloadSchemaType.KEYWORD,
    "document_id": models.PayloadSchemaType.KEYWORD,
    "category": models.PayloadSchemaType.KEYWORD,
    "version_id": models.PayloadSchemaType.KEYWORD,
    "status": models.PayloadSchemaType.KEYWORD,
    "audience": models.PayloadSchemaType.KEYWORD,
    "language": models.PayloadSchemaType.KEYWORD,
    "source_type": models.PayloadSchemaType.KEYWORD,
    "canonical_url": models.PayloadSchemaType.KEYWORD,
    "requested_url": models.PayloadSchemaType.KEYWORD,
    "final_url": models.PayloadSchemaType.KEYWORD,
    "canonical_path": models.PayloadSchemaType.KEYWORD,
    "requested_path": models.PayloadSchemaType.KEYWORD,
    "final_path": models.PayloadSchemaType.KEYWORD,
    "product": models.PayloadSchemaType.KEYWORD,
    "product.sku": models.PayloadSchemaType.KEYWORD,
    "product.mpn": models.PayloadSchemaType.KEYWORD,
    "region": models.PayloadSchemaType.KEYWORD,
    "is_active": models.PayloadSchemaType.BOOL,
    "snapshot_id": models.PayloadSchemaType.KEYWORD,
}


class QdrantKnowledgeAdapter:
    def __init__(
        self,
        *,
        url: str,
        trust_env: bool = False,
        collection_name: str,
        embedding_provider: EmbeddingProviderPort,
        sparse_embedding_provider: SparseEmbeddingProviderPort,
        reranker: KnowledgeRerankerPort,
        vector_size: int,
        tenant_candidate_limit: int = 40,
        global_candidate_limit: int = 30,
        rerank_candidate_limit: int = 30,
        tenant_weight: float = 0.65,
        global_weight: float = 0.35,
        maintenance_timeout_seconds: int = 900,
    ) -> None:
        if tenant_weight <= 0 or global_weight <= 0:
            raise ValueError("knowledge partition weights must be positive")
        if maintenance_timeout_seconds < 60:
            raise ValueError("Qdrant maintenance timeout must be at least 60 seconds")
        self._client = AsyncQdrantClient(
            url=url,
            timeout=30.0,
            check_compatibility=False,
            trust_env=trust_env,
        )
        self._maintenance_client = AsyncQdrantClient(
            url=url,
            timeout=float(maintenance_timeout_seconds),
            check_compatibility=False,
            trust_env=trust_env,
        )
        self._collection_name = collection_name
        self._embedding_provider = embedding_provider
        self._sparse_embedding_provider = sparse_embedding_provider
        self._reranker = reranker
        self._vector_size = vector_size
        self._tenant_candidate_limit = tenant_candidate_limit
        self._global_candidate_limit = global_candidate_limit
        self._rerank_candidate_limit = rerank_candidate_limit
        self._maintenance_timeout_seconds = maintenance_timeout_seconds
        self._collection_ready = False
        total_weight = tenant_weight + global_weight
        self._tenant_weight = tenant_weight / total_weight
        self._global_weight = global_weight / total_weight

    async def initialize(self) -> None:
        if not await self._client.collection_exists(self._collection_name):
            await self._client.create_collection(
                collection_name=self._collection_name,
                vectors_config={
                    _DENSE_VECTOR: models.VectorParams(
                        size=self._vector_size,
                        distance=models.Distance.COSINE,
                    )
                },
                sparse_vectors_config={
                    _SPARSE_VECTOR: models.SparseVectorParams(
                        index=models.SparseIndexParams(on_disk=False)
                    )
                },
            )
        collection = await self._client.get_collection(self._collection_name)
        _assert_hybrid_collection(collection, self._collection_name)
        existing = set(collection.payload_schema)
        for field_name, schema in _PAYLOAD_INDEXES.items():
            if field_name not in existing:
                await self._client.create_payload_index(
                    collection_name=self._collection_name,
                    field_name=field_name,
                    field_schema=schema,
                    wait=True,
                )
        self._collection_ready = True

    async def search(self, query: KnowledgeQuery) -> list[KnowledgeEvidence]:
        if not self._collection_ready:
            if not await self._client.collection_exists(self._collection_name):
                return []
            self._collection_ready = True
        dense_result, sparse_result = await asyncio.gather(
            self._embedding_provider.embed([query.text]),
            self._sparse_embedding_provider.embed_sparse([query.text]),
            return_exceptions=True,
        )
        dense_vector = None if isinstance(dense_result, Exception) else dense_result[0]
        sparse_vector = None if isinstance(sparse_result, Exception) else sparse_result[0]
        if dense_vector is None and sparse_vector is None:
            raise RuntimeError("both dense and sparse retrieval embeddings failed")
        tenant_results, global_results = await asyncio.gather(
            self._search_partition(
                query=query,
                partition_id=query.tenant_id,
                dense_vector=dense_vector,
                sparse_vector=sparse_vector,
                limit=self._tenant_candidate_limit,
            ),
            self._search_partition(
                query=query,
                partition_id=GLOBAL_KNOWLEDGE_PARTITION,
                dense_vector=dense_vector,
                sparse_vector=sparse_vector,
                limit=self._global_candidate_limit,
            ),
        )
        candidates = _weighted_partition_fusion(
            tenant_results=tenant_results,
            global_results=global_results,
            tenant_weight=self._tenant_weight,
            global_weight=self._global_weight,
            limit=self._rerank_candidate_limit,
        )
        return await self._reranker.rerank(
            query=query,
            candidates=candidates,
            limit=query.limit,
        )

    async def _search_partition(
        self,
        *,
        query: KnowledgeQuery,
        partition_id: str,
        dense_vector: Sequence[float] | None,
        sparse_vector,
        limit: int,
    ) -> list[KnowledgeEvidence]:
        query_filter = _partition_filter(query, partition_id)
        prefetch: list[models.Prefetch] = []
        if dense_vector is not None:
            prefetch.append(
                models.Prefetch(
                    query=list(dense_vector),
                    using=_DENSE_VECTOR,
                    filter=query_filter,
                    limit=limit,
                )
            )
        if sparse_vector is not None and sparse_vector.indices:
            prefetch.append(
                models.Prefetch(
                    query=models.SparseVector(
                        indices=list(sparse_vector.indices),
                        values=list(sparse_vector.values),
                    ),
                    using=_SPARSE_VECTOR,
                    filter=query_filter,
                    limit=limit,
                )
            )
        retrieval_mode = (
            "hybrid"
            if dense_vector is not None and sparse_vector is not None
            else "dense_only"
            if dense_vector is not None
            else "sparse_only"
        )
        if len(prefetch) > 1:
            response = await self._client.query_points(
                collection_name=self._collection_name,
                prefetch=prefetch,
                query=models.FusionQuery(fusion=models.Fusion.RRF),
                limit=limit,
                with_payload=True,
            )
        elif dense_vector is not None:
            response = await self._client.query_points(
                collection_name=self._collection_name,
                query=list(dense_vector),
                using=_DENSE_VECTOR,
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            )
        else:
            response = await self._client.query_points(
                collection_name=self._collection_name,
                query=models.SparseVector(
                    indices=list(sparse_vector.indices),
                    values=list(sparse_vector.values),
                ),
                using=_SPARSE_VECTOR,
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            )
        evidence: list[KnowledgeEvidence] = []
        now = datetime.now(UTC)
        for point in response.points:
            payload = dict(point.payload or {})
            _assert_payload_scope(payload, query)
            if not _is_effective(payload, now):
                continue
            evidence.append(
                KnowledgeEvidence(
                    chunk_id=str(payload.get("chunk_id", point.id)),
                    document_id=str(payload.get("document_id", "")),
                    text=str(payload.get("context_text") or payload.get("text", "")),
                    score=float(point.score),
                    source=str(payload.get("source", "")),
                    metadata={
                        **payload,
                        "retrieval_mode": retrieval_mode,
                        "language_fallback": _language(payload.get("language"))
                        != _language(query.language),
                    },
                )
            )
        return evidence

    async def upsert(self, chunks: Sequence[KnowledgeChunk]) -> None:
        await self.initialize()
        points = []
        for chunk in chunks:
            if chunk.sparse_vector is None:
                raise ValueError("hybrid knowledge chunks require a sparse vector")
            scope = str(chunk.metadata.get("knowledge_scope") or "tenant")
            partition_id = GLOBAL_KNOWLEDGE_PARTITION if scope == "global" else chunk.tenant_id
            points.append(
                models.PointStruct(
                    id=chunk.chunk_id,
                    vector={
                        _DENSE_VECTOR: list(chunk.vector),
                        _SPARSE_VECTOR: models.SparseVector(
                            indices=list(chunk.sparse_vector.indices),
                            values=list(chunk.sparse_vector.values),
                        ),
                    },
                    payload={
                        **chunk.metadata,
                        "partition_id": partition_id,
                        "knowledge_scope": scope,
                        "tenant_id": chunk.tenant_id,
                        "chunk_id": chunk.chunk_id,
                        "document_id": chunk.document_id,
                        "version_id": chunk.version_id,
                        "text": chunk.text,
                        "is_active": bool(chunk.metadata.get("is_active", True)),
                    },
                )
            )
        if points:
            await self._client.upsert(
                collection_name=self._collection_name,
                points=points,
                wait=True,
            )

    async def delete_document(self, *, tenant_id: str, document_id: str) -> None:
        if not await self._client.collection_exists(self._collection_name):
            return
        await self._client.delete(
            collection_name=self._collection_name,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[_match("tenant_id", tenant_id), _match("document_id", document_id)]
                )
            ),
            wait=True,
        )

    async def activate_document_version(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str | None,
    ) -> None:
        if not await self._maintenance_client.collection_exists(self._collection_name):
            if version_id is not None:
                raise RuntimeError("cannot activate a document version without a Qdrant collection")
            return
        document_filter = models.Filter(
            must=[_match("tenant_id", tenant_id), _match("document_id", document_id)]
        )
        if version_id is None:
            await self._maintenance_client.set_payload(
                collection_name=self._collection_name,
                payload={"is_active": False},
                points=models.FilterSelector(filter=document_filter),
                wait=True,
                timeout=self._maintenance_timeout_seconds,
            )
            return
        target_filter = models.Filter(
            must=[
                _match("tenant_id", tenant_id),
                _match("document_id", document_id),
                _match("version_id", version_id),
            ]
        )
        target_count = await self._maintenance_client.count(
            collection_name=self._collection_name,
            count_filter=target_filter,
            exact=True,
            timeout=self._maintenance_timeout_seconds,
        )
        if int(target_count.count) == 0:
            raise RuntimeError("cannot activate a document version with no Qdrant points")
        await self._maintenance_client.set_payload(
            collection_name=self._collection_name,
            payload={"is_active": True},
            points=models.FilterSelector(filter=target_filter),
            wait=True,
            timeout=self._maintenance_timeout_seconds,
        )
        await self._maintenance_client.set_payload(
            collection_name=self._collection_name,
            payload={"is_active": False},
            points=models.FilterSelector(
                filter=models.Filter(
                    must=[_match("tenant_id", tenant_id), _match("document_id", document_id)],
                    must_not=[_match("version_id", version_id)],
                )
            ),
            wait=True,
            timeout=self._maintenance_timeout_seconds,
        )

    async def activate_site_versions(
        self,
        *,
        tenant_id: str,
        site_id: str,
        version_ids: Sequence[str],
        expected_point_count: int | None = None,
    ) -> None:
        target_versions = tuple(dict.fromkeys(str(value) for value in version_ids if str(value)))
        if not await self._maintenance_client.collection_exists(self._collection_name):
            if target_versions or bool(expected_point_count):
                raise RuntimeError("cannot activate site versions without a Qdrant collection")
            return
        site_filter = models.Filter(
            must=[
                _match("tenant_id", tenant_id),
                _match("site_id", site_id),
                _match("source_type", "website_html"),
            ]
        )
        if not target_versions:
            await self._maintenance_client.set_payload(
                collection_name=self._collection_name,
                payload={"is_active": False},
                points=models.FilterSelector(filter=site_filter),
                wait=True,
                timeout=self._maintenance_timeout_seconds,
            )
            return
        target_filter = models.Filter(
            must=[
                _match("tenant_id", tenant_id),
                _match("site_id", site_id),
                _match("source_type", "website_html"),
                _match_any("version_id", target_versions),
            ]
        )
        target_count = await self._maintenance_client.count(
            collection_name=self._collection_name,
            count_filter=target_filter,
            exact=True,
            timeout=self._maintenance_timeout_seconds,
        )
        resolved_target_count = int(target_count.count)
        if resolved_target_count == 0:
            raise RuntimeError("cannot activate site versions with no Qdrant points")
        if expected_point_count is not None and resolved_target_count != expected_point_count:
            raise RuntimeError(
                "site activation Qdrant point mismatch: "
                f"expected {expected_point_count}, found {resolved_target_count}"
            )
        await self._maintenance_client.set_payload(
            collection_name=self._collection_name,
            payload={"is_active": True},
            points=models.FilterSelector(filter=target_filter),
            wait=True,
            timeout=self._maintenance_timeout_seconds,
        )
        await self._maintenance_client.set_payload(
            collection_name=self._collection_name,
            payload={"is_active": False},
            points=models.FilterSelector(
                filter=models.Filter(
                    must=site_filter.must,
                    must_not=[_match_any("version_id", target_versions)],
                )
            ),
            wait=True,
            timeout=self._maintenance_timeout_seconds,
        )
        if expected_point_count is not None:
            active_count = await self._maintenance_client.count(
                collection_name=self._collection_name,
                count_filter=models.Filter(
                    must=[
                        _match("tenant_id", tenant_id),
                        _match("site_id", site_id),
                        _match("source_type", "website_html"),
                        _match("is_active", True),
                    ]
                ),
                exact=True,
                timeout=self._maintenance_timeout_seconds,
            )
            if int(active_count.count) != expected_point_count:
                raise RuntimeError(
                    "site activation verification failed: "
                    f"expected {expected_point_count}, found {int(active_count.count)}"
                )

    async def discard_snapshot(
        self,
        *,
        tenant_id: str,
        site_id: str,
        snapshot_id: str,
    ) -> None:
        if not await self._maintenance_client.collection_exists(self._collection_name):
            return
        await self._maintenance_client.delete(
            collection_name=self._collection_name,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        _match("tenant_id", tenant_id),
                        _match("site_id", site_id),
                        _match("snapshot_id", snapshot_id),
                        _match("is_active", False),
                    ]
                )
            ),
            wait=True,
            timeout=self._maintenance_timeout_seconds,
        )

    async def discard_staged_document(
        self,
        *,
        tenant_id: str,
        site_id: str,
        snapshot_id: str,
        document_id: str,
    ) -> None:
        if not await self._maintenance_client.collection_exists(self._collection_name):
            return
        await self._maintenance_client.delete(
            collection_name=self._collection_name,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        _match("tenant_id", tenant_id),
                        _match("site_id", site_id),
                        _match("snapshot_id", snapshot_id),
                        _match("document_id", document_id),
                        _match("is_active", False),
                    ]
                )
            ),
            wait=True,
            timeout=self._maintenance_timeout_seconds,
        )

    async def count_snapshot_points(
        self,
        *,
        tenant_id: str,
        site_id: str,
        snapshot_id: str,
    ) -> int:
        if not await self._client.collection_exists(self._collection_name):
            return 0
        result = await self._maintenance_client.count(
            collection_name=self._collection_name,
            count_filter=models.Filter(
                must=[
                    _match("tenant_id", tenant_id),
                    _match("site_id", site_id),
                    _match("snapshot_id", snapshot_id),
                    _match("source_type", "website_html"),
                ]
            ),
            exact=True,
            timeout=self._maintenance_timeout_seconds,
        )
        return int(result.count)

    async def audit_site_publication(
        self,
        *,
        tenant_id: str,
        site_id: str,
        target_version_ids: Sequence[str],
    ) -> KnowledgeIndexSiteAudit:
        if not await self._maintenance_client.collection_exists(self._collection_name):
            return KnowledgeIndexSiteAudit(
                collection_exists=False,
                site_point_count=0,
                active_point_count=0,
                inactive_point_count=0,
                target_point_count=0,
                target_active_point_count=0,
                active_version_ids=(),
            )

        target_versions = tuple(
            dict.fromkeys(str(value) for value in target_version_ids if str(value))
        )
        site_filter = models.Filter(
            must=[
                _match("tenant_id", tenant_id),
                _match("site_id", site_id),
                _match("source_type", "website_html"),
            ]
        )
        active_filter = models.Filter(must=[*site_filter.must, _match("is_active", True)])
        site_count_result, active_count_result = await asyncio.gather(
            self._maintenance_client.count(
                collection_name=self._collection_name,
                count_filter=site_filter,
                exact=True,
                timeout=self._maintenance_timeout_seconds,
            ),
            self._maintenance_client.count(
                collection_name=self._collection_name,
                count_filter=active_filter,
                exact=True,
                timeout=self._maintenance_timeout_seconds,
            ),
        )
        target_point_count = 0
        target_active_point_count = 0
        if target_versions:
            target_filter = models.Filter(
                must=[*site_filter.must, _match_any("version_id", target_versions)]
            )
            target_active_filter = models.Filter(
                must=[*target_filter.must, _match("is_active", True)]
            )
            target_count_result, target_active_count_result = await asyncio.gather(
                self._maintenance_client.count(
                    collection_name=self._collection_name,
                    count_filter=target_filter,
                    exact=True,
                    timeout=self._maintenance_timeout_seconds,
                ),
                self._maintenance_client.count(
                    collection_name=self._collection_name,
                    count_filter=target_active_filter,
                    exact=True,
                    timeout=self._maintenance_timeout_seconds,
                ),
            )
            target_point_count = int(target_count_result.count)
            target_active_point_count = int(target_active_count_result.count)

        active_version_ids: set[str] = set()
        offset = None
        while True:
            points, offset = await self._maintenance_client.scroll(
                collection_name=self._collection_name,
                scroll_filter=active_filter,
                limit=1000,
                offset=offset,
                with_payload=["version_id"],
                with_vectors=False,
                timeout=self._maintenance_timeout_seconds,
            )
            for point in points:
                version_id = str((point.payload or {}).get("version_id") or "").strip()
                if version_id:
                    active_version_ids.add(version_id)
            if offset is None:
                break

        site_point_count = int(site_count_result.count)
        active_point_count = int(active_count_result.count)
        return KnowledgeIndexSiteAudit(
            collection_exists=True,
            site_point_count=site_point_count,
            active_point_count=active_point_count,
            inactive_point_count=max(0, site_point_count - active_point_count),
            target_point_count=target_point_count,
            target_active_point_count=target_active_point_count,
            active_version_ids=tuple(sorted(active_version_ids)),
        )

    async def check(self) -> None:
        if not await self._client.collection_exists(self._collection_name):
            raise RuntimeError("knowledge collection is unavailable")
        collection = await self._client.get_collection(self._collection_name)
        _assert_hybrid_collection(collection, self._collection_name)

    async def close(self) -> None:
        await self._client.close()
        await self._maintenance_client.close()


def _partition_filter(query: KnowledgeQuery, partition_id: str) -> models.Filter:
    must = [
        _match("partition_id", partition_id),
        _match("audience", query.audience),
        _match("status", "published"),
        _match("is_active", True),
    ]
    language = _language(query.language)
    language_values = tuple(dict.fromkeys((str(query.language), language)))
    if query.strict_language:
        must.append(_match_any("language", language_values))
    if partition_id == query.tenant_id:
        site_id = str(query.filters.get("site_id") or "").strip()
        if site_id:
            must.append(
                models.Filter(
                    should=[
                        _match("knowledge_scope", "tenant"),
                        _match("site_id", site_id),
                    ]
                )
            )
        else:
            must.append(_match("knowledge_scope", "tenant"))
        reference_conditions = _reference_conditions(query)
        if reference_conditions:
            must.append(models.Filter(should=reference_conditions))
    for key in ("product", "region"):
        value = query.filters.get(key)
        if value:
            must.append(_match(key, value))
    document_ids = tuple(str(value) for value in query.filters.get("document_ids", ()))
    if document_ids:
        must.append(_match_any("document_id", document_ids))
    categories = tuple(str(value) for value in query.filters.get("categories", ()))
    if categories:
        must.append(_match_any("category", categories))
    return models.Filter(must=must)


def _language(value: object) -> str:
    return str(value or "en").replace("_", "-").casefold().split("-", 1)[0]


def _reference_conditions(query: KnowledgeQuery) -> list[models.FieldCondition]:
    conditions: list[models.FieldCondition] = []
    source_urls = tuple(str(value) for value in query.filters.get("source_urls", ()))
    source_paths = tuple(str(value) for value in query.filters.get("source_paths", ()))
    identifiers = tuple(str(value) for value in query.filters.get("product_identifiers", ()))
    for field_name in ("canonical_url", "requested_url", "final_url"):
        if source_urls:
            conditions.append(_match_any(field_name, source_urls))
    for field_name in ("canonical_path", "requested_path", "final_path"):
        if source_paths:
            conditions.append(_match_any(field_name, source_paths))
    for field_name in ("product.sku", "product.mpn"):
        if identifiers:
            conditions.append(_match_any(field_name, identifiers))
    return conditions


def _weighted_partition_fusion(
    *,
    tenant_results: Sequence[KnowledgeEvidence],
    global_results: Sequence[KnowledgeEvidence],
    tenant_weight: float,
    global_weight: float,
    limit: int,
) -> list[KnowledgeEvidence]:
    fused: dict[str, KnowledgeEvidence] = {}
    for results, weight in (
        (tenant_results, tenant_weight),
        (global_results, global_weight),
    ):
        for rank, candidate in enumerate(results, start=1):
            score = weight / rank
            metadata = {**candidate.metadata, "partition_fusion_score": score}
            fused[candidate.chunk_id] = replace(candidate, score=score, metadata=metadata)
    ranked = sorted(fused.values(), key=lambda item: item.score, reverse=True)
    return ranked[:limit]


def _match(key: str, value: Any) -> models.FieldCondition:
    return models.FieldCondition(key=key, match=models.MatchValue(value=value))


def _match_any(key: str, values: Sequence[str]) -> models.FieldCondition:
    return models.FieldCondition(key=key, match=models.MatchAny(any=list(values)))


def _assert_payload_scope(payload: dict[str, Any], query: KnowledgeQuery) -> None:
    partition_id = payload.get("partition_id")
    if partition_id not in {query.tenant_id, GLOBAL_KNOWLEDGE_PARTITION}:
        raise RuntimeError("Qdrant tenant invariant failed")
    if partition_id == query.tenant_id and payload.get("tenant_id") != query.tenant_id:
        raise RuntimeError("Qdrant tenant payload invariant failed")
    if partition_id == GLOBAL_KNOWLEDGE_PARTITION and payload.get("knowledge_scope") != "global":
        raise RuntimeError("Qdrant global knowledge invariant failed")
    if payload.get("knowledge_scope") == "site":
        site_id = str(query.filters.get("site_id") or "").strip()
        if not site_id or payload.get("site_id") != site_id:
            raise RuntimeError("Qdrant site knowledge invariant failed")
    if payload.get("audience") != query.audience:
        raise RuntimeError("Qdrant audience invariant failed")
    if payload.get("status") != "published":
        raise RuntimeError("Qdrant publication invariant failed")


def _assert_hybrid_collection(collection: Any, collection_name: str) -> None:
    vectors = collection.config.params.vectors
    sparse_vectors = collection.config.params.sparse_vectors or {}
    if not isinstance(vectors, dict) or _DENSE_VECTOR not in vectors:
        raise RuntimeError(
            f"Qdrant collection {collection_name!r} uses the legacy vector schema; "
            "configure a new collection name and resynchronize knowledge"
        )
    if _SPARSE_VECTOR not in sparse_vectors:
        raise RuntimeError(
            f"Qdrant collection {collection_name!r} is missing the sparse vector schema"
        )


def _is_effective(payload: dict[str, Any], now: datetime) -> bool:
    effective_from = _parse_datetime(payload.get("effective_from"))
    effective_to = _parse_datetime(payload.get("effective_to"))
    if effective_from is not None and now < effective_from:
        return False
    return not (effective_to is not None and now > effective_to)


def _parse_datetime(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
