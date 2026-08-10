import asyncio
from collections import OrderedDict
from collections.abc import Sequence
from hashlib import sha256
from math import isfinite
from pathlib import Path
from threading import Lock
from typing import Any


class FastEmbedEmbeddingProvider:
    def __init__(
        self,
        *,
        model_name: str,
        dimension: int,
        cache_dir: Path,
        threads: int = 4,
        batch_size: int = 32,
        cache_max_entries: int = 4096,
        model: Any | None = None,
    ) -> None:
        if not model_name.strip():
            raise ValueError("local embedding model name is required")
        if dimension <= 0:
            raise ValueError("local embedding dimension must be positive")
        if threads <= 0:
            raise ValueError("local embedding threads must be positive")
        if batch_size <= 0:
            raise ValueError("local embedding batch size must be positive")
        if cache_max_entries < 0:
            raise ValueError("local embedding cache size must not be negative")
        self.dimension = dimension
        self._model_name = model_name
        self._cache_dir = cache_dir
        self._threads = threads
        self._batch_size = batch_size
        self._cache_max_entries = cache_max_entries
        self._model = model
        self._model_lock = Lock()
        self._cache_lock = Lock()
        self._vector_cache: OrderedDict[str, tuple[float, ...]] = OrderedDict()

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        requested = tuple(texts)
        if self._cache_max_entries == 0:
            return await asyncio.to_thread(self._embed_sync, requested)
        keys = tuple(sha256(text.encode("utf-8")).hexdigest() for text in requested)
        cached: dict[str, tuple[float, ...]] = {}
        with self._cache_lock:
            for key in keys:
                vector = self._vector_cache.get(key)
                if vector is not None:
                    cached[key] = vector
                    self._vector_cache.move_to_end(key)
        missing = tuple(
            (key, text)
            for key, text in dict(zip(keys, requested, strict=True)).items()
            if key not in cached
        )
        if missing:
            vectors = await asyncio.to_thread(
                self._embed_sync,
                tuple(text for _, text in missing),
            )
            with self._cache_lock:
                for (key, _), vector in zip(missing, vectors, strict=True):
                    stored = tuple(vector)
                    self._vector_cache[key] = stored
                    self._vector_cache.move_to_end(key)
                    cached[key] = stored
                while len(self._vector_cache) > self._cache_max_entries:
                    self._vector_cache.popitem(last=False)
        return [list(cached[key]) for key in keys]

    def _embed_sync(self, texts: tuple[str, ...]) -> list[list[float]]:
        try:
            embeddings = list(self._get_model().embed(texts, batch_size=self._batch_size))
        except Exception as exc:
            raise RuntimeError(f"local embedding model {self._model_name!r} failed") from exc
        if len(embeddings) != len(texts):
            raise RuntimeError("local embedding provider returned an unexpected vector count")
        vectors = [
            embedding.tolist() if hasattr(embedding, "tolist") else list(embedding)
            for embedding in embeddings
        ]
        for vector in vectors:
            if len(vector) != self.dimension:
                raise RuntimeError(
                    "local embedding dimension mismatch: "
                    f"expected {self.dimension}, received {len(vector)}"
                )
            if not all(isfinite(float(value)) for value in vector):
                raise RuntimeError("local embedding provider returned a non-finite value")
        return [[float(value) for value in vector] for vector in vectors]

    def _get_model(self) -> Any:
        if self._model is not None:
            return self._model
        with self._model_lock:
            if self._model is None:
                from fastembed import TextEmbedding

                self._cache_dir.mkdir(parents=True, exist_ok=True)
                self._model = TextEmbedding(
                    model_name=self._model_name,
                    cache_dir=str(self._cache_dir),
                    threads=self._threads,
                    providers=["CPUExecutionProvider"],
                    lazy_load=True,
                )
        return self._model
