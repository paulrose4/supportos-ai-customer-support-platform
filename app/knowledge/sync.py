from uuid import NAMESPACE_URL, uuid5

from app.domain.ports import (
    GLOBAL_KNOWLEDGE_PARTITION,
    EmbeddingProviderPort,
    KnowledgeChunk,
    KnowledgeChunkRecord,
    KnowledgeControlPlanePort,
    KnowledgeIndexerPort,
    KnowledgeSyncLeasePort,
    KnowledgeVersionDraft,
    SparseEmbeddingProviderPort,
)
from app.knowledge.chunker import MarkdownChunker
from app.knowledge.lease import InMemoryKnowledgeSyncLease
from app.knowledge.models import KnowledgeSyncReport
from app.knowledge.parser import MarkdownKnowledgeParser
from app.knowledge.scanner import KnowledgeSourceScanner


class KnowledgeSyncService:
    def __init__(
        self,
        *,
        scanner: KnowledgeSourceScanner,
        parser: MarkdownKnowledgeParser,
        chunker: MarkdownChunker,
        embedding_provider: EmbeddingProviderPort,
        sparse_embedding_provider: SparseEmbeddingProviderPort,
        indexer: KnowledgeIndexerPort,
        control_plane: KnowledgeControlPlanePort,
        lease: KnowledgeSyncLeasePort | None = None,
        lease_seconds: int = 3600,
        index_namespace: str = "hybrid-v1",
    ) -> None:
        self._scanner = scanner
        self._parser = parser
        self._chunker = chunker
        self._embedding_provider = embedding_provider
        self._sparse_embedding_provider = sparse_embedding_provider
        self._indexer = indexer
        self._control_plane = control_plane
        self._lease = lease or InMemoryKnowledgeSyncLease()
        self._lease_seconds = lease_seconds
        self._index_namespace = index_namespace

    async def sync(
        self,
        tenant_id: str,
        *,
        actor_subject_id: str | None = None,
        correlation_id: str | None = None,
        approval_reference: str | None = None,
    ) -> KnowledgeSyncReport:
        lease_token = await self._lease.acquire(
            tenant_id=tenant_id,
            ttl_seconds=self._lease_seconds,
        )
        if lease_token is None:
            raise RuntimeError("a knowledge synchronization is already active for this tenant")
        try:
            return await self._sync_unlocked(
                tenant_id,
                actor_subject_id=actor_subject_id,
                correlation_id=correlation_id,
                approval_reference=approval_reference,
            )
        finally:
            await self._lease.release(tenant_id=tenant_id, token=lease_token)

    async def _sync_unlocked(
        self,
        tenant_id: str,
        *,
        actor_subject_id: str | None = None,
        correlation_id: str | None = None,
        approval_reference: str | None = None,
    ) -> KnowledgeSyncReport:
        source_root = self._scanner.root_for(tenant_id)
        if any((actor_subject_id, correlation_id, approval_reference)):
            sync_job_id = await self._control_plane.begin_sync(
                tenant_id=tenant_id,
                vault_path=str(source_root),
                actor_subject_id=actor_subject_id,
                correlation_id=correlation_id,
                approval_reference=approval_reference,
            )
        else:
            sync_job_id = await self._control_plane.begin_sync(
                tenant_id=tenant_id, vault_path=str(source_root)
            )
        paths = self._scanner.scan(tenant_id)
        indexed_count = 0
        skipped_count = 0
        excluded_count = 0
        failed_count = 0
        errors: dict[str, str] = {}

        try:
            for path in paths:
                try:
                    document = self._parser.parse(path)
                    if document.metadata["tenant_id"] != tenant_id:
                        source_path = path.relative_to(source_root).as_posix()
                        await self._control_plane.record_ingestion_rejection(
                            tenant_id=tenant_id,
                            sync_job_id=sync_job_id,
                            source_path=source_path,
                            reason_code="tenant_mismatch",
                        )
                        excluded_count += 1
                        errors[str(path)] = (
                            "quarantined: document tenant does not match synchronization tenant"
                        )
                        continue
                    latest = await self._control_plane.get_latest_version(
                        tenant_id=tenant_id,
                        document_id=str(document.metadata["document_id"]),
                    )
                    same_content = (
                        latest is not None and latest.content_hash == document.content_hash
                    )
                    if (
                        same_content
                        and latest.index_namespace == self._index_namespace
                        and latest.index_status in {"active", "indexed"}
                    ):
                        skipped_count += 1
                        continue

                    text_chunks = self._chunker.chunk(document)
                    version_id = (
                        latest.version_id
                        if same_content and latest is not None
                        else str(
                            uuid5(
                                NAMESPACE_URL,
                                ":".join(
                                    (
                                        tenant_id,
                                        str(document.metadata["document_id"]),
                                        document.content_hash,
                                    )
                                ),
                            )
                        )
                    )
                    point_ids = [
                        str(uuid5(NAMESPACE_URL, f"{version_id}:{chunk.chunk_id}"))
                        for chunk in text_chunks
                    ]
                    source_path = document.path.relative_to(source_root).as_posix()
                    if same_content:
                        staged_version_id = version_id
                    else:
                        draft = _draft_from_document(
                            document,
                            tenant_id,
                            version_id,
                            source_path,
                            self._index_namespace,
                        )
                        staged_version_id = await self._control_plane.stage_version(
                            draft=draft,
                            chunks=[
                                KnowledgeChunkRecord(
                                    chunk_id=point_id,
                                    content_hash=chunk.content_hash,
                                    sequence=chunk.sequence,
                                    heading=chunk.heading,
                                    chunk_type=chunk.chunk_type,
                                    parent_chunk_id=chunk.parent_chunk_id,
                                    section_path=chunk.section_path,
                                    fact_keys=chunk.fact_keys,
                                )
                                for point_id, chunk in zip(point_ids, text_chunks, strict=True)
                            ],
                        )

                    document_id = str(document.metadata["document_id"])
                    previous_version_id = latest.version_id if latest is not None else None
                    if document.metadata["status"] != "published":
                        await self._indexer.activate_document_version(
                            tenant_id=tenant_id,
                            document_id=document_id,
                            version_id=None,
                        )
                        try:
                            await self._control_plane.mark_excluded(
                                tenant_id=tenant_id,
                                document_id=document_id,
                                version_id=staged_version_id,
                            )
                        except Exception:
                            await self._indexer.activate_document_version(
                                tenant_id=tenant_id,
                                document_id=document_id,
                                version_id=previous_version_id,
                            )
                            raise
                        excluded_count += 1
                        continue

                    vectors = await self._embedding_provider.embed(
                        [chunk.text for chunk in text_chunks]
                    )
                    sparse_vectors = await self._sparse_embedding_provider.embed_sparse(
                        [chunk.text for chunk in text_chunks]
                    )
                    knowledge_scope = (
                        "global" if tenant_id == GLOBAL_KNOWLEDGE_PARTITION else "tenant"
                    )
                    projection = [
                        KnowledgeChunk(
                            tenant_id=tenant_id,
                            chunk_id=point_id,
                            document_id=str(document.metadata["document_id"]),
                            version_id=staged_version_id,
                            text=chunk.text,
                            vector=vector,
                            sparse_vector=sparse_vector,
                            metadata={
                                **document.metadata,
                                "partition_id": tenant_id,
                                "knowledge_scope": knowledge_scope,
                                "source_type": document.metadata.get(
                                    "source_type", "obsidian_markdown"
                                ),
                                "is_active": False,
                                "version_id": staged_version_id,
                                "source": source_path,
                                "sequence": chunk.sequence,
                                "heading": chunk.heading,
                                "content_hash": chunk.content_hash,
                                "chunk_type": chunk.chunk_type,
                                "parent_chunk_id": chunk.parent_chunk_id,
                                "section_path": list(chunk.section_path),
                                "fact_keys": list(chunk.fact_keys),
                                "context_text": chunk.context_text,
                            },
                        )
                        for point_id, chunk, vector, sparse_vector in zip(
                            point_ids, text_chunks, vectors, sparse_vectors, strict=True
                        )
                    ]
                    await self._indexer.upsert(projection)
                    await self._control_plane.mark_indexed(
                        tenant_id=tenant_id,
                        document_id=document_id,
                        version_id=staged_version_id,
                        point_ids=point_ids,
                        index_namespace=self._index_namespace,
                        activate=False,
                    )
                    try:
                        await self._indexer.activate_document_version(
                            tenant_id=tenant_id,
                            document_id=document_id,
                            version_id=staged_version_id,
                        )
                        await self._control_plane.activate_document_version(
                            tenant_id=tenant_id,
                            document_id=document_id,
                            version_id=staged_version_id,
                        )
                    except Exception:
                        await self._indexer.activate_document_version(
                            tenant_id=tenant_id,
                            document_id=document_id,
                            version_id=previous_version_id,
                        )
                        await self._control_plane.activate_document_version(
                            tenant_id=tenant_id,
                            document_id=document_id,
                            version_id=previous_version_id,
                        )
                        raise
                    indexed_count += len(projection)
                except Exception as exc:  # noqa: BLE001
                    failed_count += 1
                    errors[str(path)] = f"{type(exc).__name__}: {exc}"

            await self._control_plane.complete_sync(
                tenant_id=tenant_id,
                sync_job_id=sync_job_id,
                discovered_count=len(paths),
                indexed_count=indexed_count,
                failed_count=failed_count,
                error_summary=errors,
            )
            return KnowledgeSyncReport(
                sync_job_id=sync_job_id,
                discovered_count=len(paths),
                indexed_count=indexed_count,
                skipped_count=skipped_count,
                excluded_count=excluded_count,
                failed_count=failed_count,
                errors=errors,
            )
        except Exception as exc:
            await self._control_plane.fail_sync(
                tenant_id=tenant_id,
                sync_job_id=sync_job_id,
                error_code=type(exc).__name__,
            )
            raise


def _draft_from_document(
    document,
    tenant_id: str,
    version_id: str,
    source_path: str,
    index_namespace: str,
) -> KnowledgeVersionDraft:
    metadata = document.metadata
    return KnowledgeVersionDraft(
        version_id=version_id,
        tenant_id=tenant_id,
        document_id=str(metadata["document_id"]),
        source_path=source_path,
        title=str(metadata["title"]),
        version=str(metadata["version"]),
        content_hash=document.content_hash,
        status=str(metadata["status"]),
        authority_level=int(metadata["authority_level"]),
        priority=int(metadata["priority"]),
        language=str(metadata["language"]),
        audience=str(metadata["audience"]),
        effective_from=_optional_string(metadata.get("effective_from")),
        effective_to=_optional_string(metadata.get("effective_to")),
        metadata={**metadata, "index_namespace": index_namespace},
        internal_links=document.internal_links,
    )


def _optional_string(value: object) -> str | None:
    if value is None or value == "":
        return None
    return str(value)
