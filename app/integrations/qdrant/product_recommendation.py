import asyncio
from collections.abc import Sequence
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import AsyncQdrantClient, models

from app.domain.models import ProductSnapshot
from app.domain.ports import (
    EmbeddingProviderPort,
    ProductRetrievalHit,
    SparseEmbeddingProviderPort,
)
from app.domain.rules import product_search_text, reciprocal_rank_fusion

_DENSE_VECTOR = "dense"
_SPARSE_VECTOR = "sparse"


class QdrantProductRecommendationAdapter:
    """Dedicated product index. Postgres remains authoritative for returned facts and links."""

    def __init__(
        self,
        *,
        url: str,
        collection_name: str,
        embedding_provider: EmbeddingProviderPort,
        sparse_embedding_provider: SparseEmbeddingProviderPort,
        vector_size: int,
        trust_env: bool = False,
        timeout_seconds: float = 30.0,
        batch_size: int = 128,
    ) -> None:
        if vector_size <= 0 or batch_size <= 0:
            raise ValueError("product index vector size and batch size must be positive")
        self._client = AsyncQdrantClient(
            url=url,
            timeout=timeout_seconds,
            check_compatibility=False,
            trust_env=trust_env,
        )
        self._collection_name = collection_name
        self._embedding_provider = embedding_provider
        self._sparse_embedding_provider = sparse_embedding_provider
        self._vector_size = vector_size
        self._batch_size = batch_size

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
        dense = collection.config.params.vectors
        sparse = collection.config.params.sparse_vectors or {}
        if (
            not isinstance(dense, dict)
            or dense.get(_DENSE_VECTOR) is None
            or _SPARSE_VECTOR not in sparse
        ):
            raise RuntimeError("product recommendation collection has incompatible vector schema")
        existing = set(collection.payload_schema)
        for field_name, field_schema in {
            "tenant_id": models.PayloadSchemaType.KEYWORD,
            "site_id": models.PayloadSchemaType.KEYWORD,
            "snapshot_id": models.PayloadSchemaType.KEYWORD,
            "product_key": models.PayloadSchemaType.KEYWORD,
            "is_active": models.PayloadSchemaType.BOOL,
        }.items():
            if field_name not in existing:
                await self._client.create_payload_index(
                    collection_name=self._collection_name,
                    field_name=field_name,
                    field_schema=field_schema,
                    wait=True,
                )

    async def replace_site_snapshot(
        self,
        *,
        tenant_id: str,
        site_id: str,
        snapshot_id: str,
        products: Sequence[ProductSnapshot],
    ) -> None:
        await self.initialize()
        for start in range(0, len(products), self._batch_size):
            batch = tuple(products[start : start + self._batch_size])
            texts = [product_search_text(product) for product in batch]
            dense_vectors, sparse_vectors = await asyncio.gather(
                self._embedding_provider.embed(texts),
                self._sparse_embedding_provider.embed_sparse(texts),
            )
            points = [
                models.PointStruct(
                    id=str(uuid5(NAMESPACE_URL, f"{tenant_id}:{site_id}:{product.product_key}")),
                    vector={
                        _DENSE_VECTOR: dense,
                        _SPARSE_VECTOR: models.SparseVector(
                            indices=list(sparse.indices),
                            values=list(sparse.values),
                        ),
                    },
                    payload={
                        "tenant_id": tenant_id,
                        "site_id": site_id,
                        "snapshot_id": snapshot_id,
                        "product_key": product.product_key,
                        "canonical_url": product.canonical_url,
                        "is_active": True,
                    },
                )
                for product, dense, sparse in zip(
                    batch,
                    dense_vectors,
                    sparse_vectors,
                    strict=True,
                )
            ]
            await self._client.upsert(
                collection_name=self._collection_name,
                points=points,
                wait=True,
            )
        await self._client.delete(
            collection_name=self._collection_name,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        _match("tenant_id", tenant_id),
                        _match("site_id", site_id),
                    ],
                    must_not=[_match("snapshot_id", snapshot_id)],
                )
            ),
            wait=True,
        )

    async def search(
        self,
        *,
        tenant_id: str,
        site_id: str | None,
        query: str,
        limit: int,
    ) -> Sequence[ProductRetrievalHit]:
        if not await self._client.collection_exists(self._collection_name):
            return ()
        dense_vectors, sparse_vectors = await asyncio.gather(
            self._embedding_provider.embed([query]),
            self._sparse_embedding_provider.embed_sparse([query]),
        )
        query_filter = models.Filter(
            must=[
                _match("tenant_id", tenant_id),
                *([_match("site_id", site_id)] if site_id else []),
                _match("is_active", True),
            ]
        )
        dense_response = await self._client.query_points(
            collection_name=self._collection_name,
            query=list(dense_vectors[0]),
            using=_DENSE_VECTOR,
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        )
        sparse_vector = sparse_vectors[0]
        sparse_response = None
        if sparse_vector.indices:
            sparse_response = await self._client.query_points(
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
        dense = _score_map(dense_response.points)
        sparse = _score_map(sparse_response.points) if sparse_response is not None else {}
        keys = tuple(dict.fromkeys((*dense, *sparse)))
        dense_scores = [_normalized_score(dense, key) for key in keys]
        sparse_scores = [_normalized_score(sparse, key) for key in keys]
        fusion_scores = reciprocal_rank_fusion((dense_scores, sparse_scores))
        hits = [
            ProductRetrievalHit(key, dense_score, sparse_score, fusion_score)
            for key, dense_score, sparse_score, fusion_score in zip(
                keys,
                dense_scores,
                sparse_scores,
                fusion_scores,
                strict=True,
            )
        ]
        return tuple(sorted(hits, key=lambda item: item.fusion_score, reverse=True)[:limit])

    async def close(self) -> None:
        await self._client.close()


def _match(key: str, value: str | bool) -> models.FieldCondition:
    return models.FieldCondition(key=key, match=models.MatchValue(value=value))


def _score_map(points) -> dict[str, float]:
    result: dict[str, float] = {}
    for point in points:
        payload = point.payload or {}
        product_key = str(payload.get("product_key") or "")
        if product_key:
            result[product_key] = max(0.0, float(point.score))
    return result


def _normalized_score(scores: dict[str, float], key: str) -> float:
    maximum = max(scores.values(), default=0.0)
    return scores.get(key, 0.0) / maximum if maximum else 0.0
