from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class ChatModelRequest:
    messages: tuple[dict[str, str], ...]
    metadata: dict[str, Any] = field(default_factory=dict)
    max_output_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class ChatModelResult:
    text: str
    model: str
    metadata: dict[str, Any] = field(default_factory=dict)


class ChatModelPort(Protocol):
    async def generate(self, request: ChatModelRequest) -> ChatModelResult: ...


class EmbeddingProviderPort(Protocol):
    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


@dataclass(frozen=True, slots=True)
class SparseEmbedding:
    indices: tuple[int, ...]
    values: tuple[float, ...]


class SparseEmbeddingProviderPort(Protocol):
    async def embed_sparse(self, texts: Sequence[str]) -> list[SparseEmbedding]: ...
