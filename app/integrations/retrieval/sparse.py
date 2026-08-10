import math
from collections import Counter
from collections.abc import Sequence
from hashlib import blake2b

from app.domain.ports import SparseEmbedding
from app.integrations.retrieval.text import tokenize_search_text


class HashingSparseEmbeddingProvider:
    def __init__(self, *, dimensions: int = 1_000_003) -> None:
        if dimensions < 1024:
            raise ValueError("sparse embedding dimensions must be at least 1024")
        self._dimensions = dimensions

    async def embed_sparse(self, texts: Sequence[str]) -> list[SparseEmbedding]:
        return [_embed_text(text, self._dimensions) for text in texts]


def _embed_text(text: str, dimensions: int) -> SparseEmbedding:
    counts = Counter(tokenize_search_text(text))
    buckets: dict[int, float] = {}
    for token, count in counts.items():
        index = int.from_bytes(blake2b(token.encode("utf-8"), digest_size=8).digest(), "big")
        index %= dimensions
        buckets[index] = buckets.get(index, 0.0) + 1.0 + math.log(float(count))
    norm = math.sqrt(sum(value * value for value in buckets.values())) or 1.0
    ordered = sorted(buckets.items())
    return SparseEmbedding(
        indices=tuple(index for index, _ in ordered),
        values=tuple(value / norm for _, value in ordered),
    )
