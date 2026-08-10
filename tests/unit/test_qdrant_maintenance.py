from types import SimpleNamespace
from typing import Any

import pytest

from app.integrations.qdrant import QdrantKnowledgeAdapter
from app.integrations.retrieval import (
    DeterministicKnowledgeReranker,
    HashingSparseEmbeddingProvider,
)
from tests.fakes.adapters import DeterministicEmbeddingProvider


class _RecordingQdrantClient:
    def __init__(
        self,
        counts: tuple[int, ...] = (1,),
        *,
        collection_exists: bool = True,
    ) -> None:
        self.payload_calls: list[dict[str, Any]] = []
        self.delete_calls: list[dict[str, Any]] = []
        self.count_calls: list[dict[str, Any]] = []
        self.counts = list(counts)
        self.has_collection = collection_exists

    async def collection_exists(self, _collection_name: str) -> bool:
        return self.has_collection

    async def set_payload(self, **values: Any) -> None:
        self.payload_calls.append(values)

    async def count(self, **values: Any) -> SimpleNamespace:
        self.count_calls.append(values)
        return SimpleNamespace(count=self.counts.pop(0))

    async def delete(self, **values: Any) -> None:
        self.delete_calls.append(values)


def _adapter(timeout: int = 300) -> QdrantKnowledgeAdapter:
    return QdrantKnowledgeAdapter(
        url="http://qdrant.invalid",
        collection_name="knowledge",
        embedding_provider=DeterministicEmbeddingProvider(),
        sparse_embedding_provider=HashingSparseEmbeddingProvider(),
        reranker=DeterministicKnowledgeReranker(),
        vector_size=2,
        maintenance_timeout_seconds=timeout,
    )


async def test_bulk_activation_uses_maintenance_timeout() -> None:
    adapter = _adapter(420)
    client = _RecordingQdrantClient()
    adapter._maintenance_client = client  # type: ignore[assignment]

    await adapter.activate_site_versions(
        tenant_id="tenant-a",
        site_id="site-a",
        version_ids=("version-a",),
    )

    assert len(client.payload_calls) == 2
    assert {call["timeout"] for call in client.payload_calls} == {420}


async def test_bulk_activation_enables_target_before_disabling_stale_versions() -> None:
    adapter = _adapter()
    client = _RecordingQdrantClient((2, 2))
    adapter._maintenance_client = client  # type: ignore[assignment]

    await adapter.activate_site_versions(
        tenant_id="tenant-a",
        site_id="site-a",
        version_ids=("version-a", "version-b"),
        expected_point_count=2,
    )

    assert [call["payload"]["is_active"] for call in client.payload_calls] == [True, False]
    stale_filter = client.payload_calls[1]["points"].filter
    assert stale_filter.must_not
    assert len(client.count_calls) == 2


async def test_bulk_activation_refuses_point_mismatch_before_mutation() -> None:
    adapter = _adapter()
    client = _RecordingQdrantClient((1,))
    adapter._maintenance_client = client  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="expected 2, found 1"):
        await adapter.activate_site_versions(
            tenant_id="tenant-a",
            site_id="site-a",
            version_ids=("version-a",),
            expected_point_count=2,
        )

    assert not client.payload_calls


async def test_bulk_activation_refuses_missing_collection_for_nonempty_target() -> None:
    adapter = _adapter()
    client = _RecordingQdrantClient(collection_exists=False)
    adapter._maintenance_client = client  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="without a Qdrant collection"):
        await adapter.activate_site_versions(
            tenant_id="tenant-a",
            site_id="site-a",
            version_ids=("version-a",),
            expected_point_count=1,
        )

    assert not client.payload_calls


async def test_snapshot_discard_uses_maintenance_timeout() -> None:
    adapter = _adapter(420)
    client = _RecordingQdrantClient()
    adapter._maintenance_client = client  # type: ignore[assignment]

    await adapter.discard_snapshot(
        tenant_id="tenant-a",
        site_id="site-a",
        snapshot_id="snapshot-a",
    )

    assert len(client.delete_calls) == 1
    assert client.delete_calls[0]["timeout"] == 420
