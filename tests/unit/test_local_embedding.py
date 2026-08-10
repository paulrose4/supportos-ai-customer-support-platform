from pathlib import Path

import pytest

from app.integrations.llm import FastEmbedEmbeddingProvider


class StubEmbeddingModel:
    def __init__(self, vectors: list[list[float]]) -> None:
        self._vectors = vectors
        self.calls: list[tuple[tuple[str, ...], int]] = []

    def embed(self, texts: tuple[str, ...], *, batch_size: int):  # type: ignore[no-untyped-def]
        self.calls.append((texts, batch_size))
        return iter(self._vectors)


@pytest.mark.asyncio
async def test_fastembed_adapter_returns_float_vectors(tmp_path: Path) -> None:
    model = StubEmbeddingModel([[1, 0, 0], [0, 1, 0]])
    provider = FastEmbedEmbeddingProvider(
        model_name="test-model",
        dimension=3,
        cache_dir=tmp_path,
        threads=2,
        batch_size=8,
        model=model,
    )

    vectors = await provider.embed(["hello", "你好"])

    assert vectors == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    assert model.calls == [(("hello", "你好"), 8)]


@pytest.mark.asyncio
async def test_fastembed_adapter_skips_model_for_empty_input(tmp_path: Path) -> None:
    model = StubEmbeddingModel([])
    provider = FastEmbedEmbeddingProvider(
        model_name="test-model",
        dimension=3,
        cache_dir=tmp_path,
        model=model,
    )

    assert await provider.embed([]) == []
    assert model.calls == []


@pytest.mark.asyncio
async def test_fastembed_adapter_rejects_dimension_mismatch(tmp_path: Path) -> None:
    provider = FastEmbedEmbeddingProvider(
        model_name="test-model",
        dimension=3,
        cache_dir=tmp_path,
        model=StubEmbeddingModel([[1, 0]]),
    )

    with pytest.raises(RuntimeError, match="dimension mismatch"):
        await provider.embed(["hello"])


@pytest.mark.asyncio
async def test_fastembed_adapter_rejects_non_finite_values(tmp_path: Path) -> None:
    provider = FastEmbedEmbeddingProvider(
        model_name="test-model",
        dimension=2,
        cache_dir=tmp_path,
        model=StubEmbeddingModel([[float("nan"), 0]]),
    )

    with pytest.raises(RuntimeError, match="non-finite"):
        await provider.embed(["hello"])


@pytest.mark.asyncio
async def test_fastembed_adapter_reuses_hashed_vector_cache(tmp_path: Path) -> None:
    model = StubEmbeddingModel([[1, 0, 0]])
    provider = FastEmbedEmbeddingProvider(
        model_name="test-model",
        dimension=3,
        cache_dir=tmp_path,
        model=model,
    )

    first = await provider.embed(["repeat me"])
    second = await provider.embed(["repeat me"])

    assert first == second == [[1.0, 0.0, 0.0]]
    assert model.calls == [(("repeat me",), 32)]
