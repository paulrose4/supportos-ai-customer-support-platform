import asyncio
import math
from collections.abc import Sequence
from pathlib import Path
from threading import Lock
from typing import Any


class FastEmbedProductReranker:
    """Local cross-encoder adapter; model loading and inference stay outside application code."""

    def __init__(
        self,
        *,
        model_name: str = "BAAI/bge-reranker-base",
        cache_dir: Path = Path("./.cache/fastembed"),
        threads: int = 4,
        batch_size: int = 16,
        timeout_seconds: float = 0.8,
        model: Any | None = None,
    ) -> None:
        if not model_name.strip():
            raise ValueError("product reranker model name is required")
        if threads <= 0 or batch_size <= 0 or timeout_seconds <= 0:
            raise ValueError("product reranker runtime settings must be positive")
        self._model_name = model_name
        self._cache_dir = cache_dir
        self._threads = threads
        self._batch_size = batch_size
        self._timeout_seconds = timeout_seconds
        self._model = model
        self._model_lock = Lock()

    async def score(self, *, query: str, documents: Sequence[str]) -> Sequence[float]:
        if not documents:
            return ()
        try:
            async with asyncio.timeout(self._timeout_seconds):
                return await asyncio.to_thread(self._score_sync, query, tuple(documents))
        except TimeoutError as exc:
            raise RuntimeError("product cross-encoder timed out") from exc

    async def prewarm(self) -> None:
        await self.score(query="product recommendation", documents=("product",))

    def _score_sync(self, query: str, documents: tuple[str, ...]) -> tuple[float, ...]:
        try:
            raw_scores = tuple(
                float(value)
                for value in self._get_model().rerank(
                    query,
                    documents,
                    batch_size=self._batch_size,
                )
            )
        except Exception as exc:
            raise RuntimeError(f"product reranker {self._model_name!r} failed") from exc
        if len(raw_scores) != len(documents) or not all(
            math.isfinite(value) for value in raw_scores
        ):
            raise RuntimeError("product reranker returned invalid scores")
        return tuple(_sigmoid(value) for value in raw_scores)

    def _get_model(self) -> Any:
        if self._model is not None:
            return self._model
        with self._model_lock:
            if self._model is None:
                from fastembed.rerank.cross_encoder import TextCrossEncoder

                self._cache_dir.mkdir(parents=True, exist_ok=True)
                self._model = TextCrossEncoder(
                    model_name=self._model_name,
                    cache_dir=str(self._cache_dir),
                    threads=self._threads,
                    providers=["CPUExecutionProvider"],
                    lazy_load=True,
                )
        return self._model


def _sigmoid(value: float) -> float:
    if value >= 0:
        factor = math.exp(-value)
        return 1.0 / (1.0 + factor)
    factor = math.exp(value)
    return factor / (1.0 + factor)
