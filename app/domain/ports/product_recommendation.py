from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.domain.models.product_catalog import ProductSnapshot
from app.domain.models.recommendation import (
    CandidateReview,
    CandidateReviewInput,
    ProductRecommendationMode,
    RecommendationPlannerAdvice,
)


class ProductRerankerPort(Protocol):
    async def score(self, *, query: str, documents: Sequence[str]) -> Sequence[float]: ...


class RecommendationPlannerPort(Protocol):
    async def plan(
        self,
        *,
        query: str,
        mode: ProductRecommendationMode,
    ) -> RecommendationPlannerAdvice: ...


class CandidateReviewPort(Protocol):
    async def review(
        self,
        *,
        query: str,
        candidates: Sequence[CandidateReviewInput],
    ) -> Sequence[CandidateReview]: ...


@dataclass(frozen=True, slots=True)
class ProductRetrievalHit:
    product_key: str
    dense_score: float = 0.0
    sparse_score: float = 0.0
    fusion_score: float = 0.0


class ProductCandidateRetrieverPort(Protocol):
    async def search(
        self,
        *,
        tenant_id: str,
        site_id: str | None,
        query: str,
        limit: int,
    ) -> Sequence[ProductRetrievalHit]: ...


class ProductRecommendationIndexPort(ProductCandidateRetrieverPort, Protocol):
    async def initialize(self) -> None: ...

    async def replace_site_snapshot(
        self,
        *,
        tenant_id: str,
        site_id: str,
        snapshot_id: str,
        products: Sequence[ProductSnapshot],
    ) -> None: ...

    async def close(self) -> None: ...
