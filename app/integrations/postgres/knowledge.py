from collections.abc import Sequence
from datetime import UTC, datetime
from hashlib import sha256
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.models import ProductSnapshotActivation
from app.domain.ports import (
    ActiveWebDocumentSnapshot,
    KnowledgeChunkRecord,
    KnowledgeControlPlaneSiteAudit,
    KnowledgeSitePublicationState,
    KnowledgeVersionDraft,
    KnowledgeVersionSchemaInvariantError,
    KnowledgeVersionSnapshot,
    ProductIdentifierConflict,
)
from app.domain.rules.knowledge_metadata import (
    validate_chunk_metadata,
    validate_document_metadata,
)
from app.integrations.postgres.models import (
    AuditEventModel,
    KnowledgeChunkManifestModel,
    KnowledgeConflictModel,
    KnowledgeDocumentModel,
    KnowledgeDocumentVersionModel,
    KnowledgeLinkModel,
    KnowledgeSyncJobModel,
    ProductCatalogSnapshotModel,
    SupportSiteModel,
    WebSyncJobItemModel,
    WebSyncJobModel,
)
from app.integrations.postgres.product_catalog import activate_product_snapshot_in_session


def _care_locale_base(value: object) -> str:
    return str(value or "").strip().replace("_", "-").casefold().split("-", 1)[0]


def _approved_care_version_supports_language(
    metadata: dict,
    language: str | None,
) -> bool:
    if (
        not str(metadata.get("care_pack_id") or "").strip()
        or not str(metadata.get("care_pack_version") or "").strip()
        or metadata.get("care_scope") != "company_global"
    ):
        return False
    if not language:
        return True
    requested = _care_locale_base(language)
    declared = metadata.get("care_locales")
    if isinstance(declared, list) and declared:
        declared_bases = {_care_locale_base(value) for value in declared}
        if requested not in declared_bases:
            return False
    if metadata.get("category") == "product_care_sop":
        values = metadata.get("approved_steps", [])
        return bool(values) and all(
            isinstance(step, dict)
            and isinstance(step.get("instructions"), dict)
            and any(
                _care_locale_base(key) == requested and str(value or "").strip()
                for key, value in step["instructions"].items()
            )
            for step in values
        )
    responses = metadata.get("approved_responses")
    return isinstance(responses, dict) and any(
        _care_locale_base(key) == requested and str(value or "").strip()
        for key, value in responses.items()
    )


class PostgreSQLKnowledgeControlPlaneAdapter:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def has_active_approved_care_sop(self, language: str | None = None) -> bool:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(KnowledgeDocumentVersionModel)
                .join(
                    KnowledgeDocumentModel,
                    (KnowledgeDocumentModel.tenant_id == KnowledgeDocumentVersionModel.tenant_id)
                    & (
                        KnowledgeDocumentModel.current_version_id
                        == KnowledgeDocumentVersionModel.version_id
                    ),
                )
                .where(
                    KnowledgeDocumentVersionModel.tenant_id == "__global__",
                    KnowledgeDocumentVersionModel.status == "published",
                    KnowledgeDocumentVersionModel.index_status == "active",
                    KnowledgeDocumentVersionModel.authority_level >= 80,
                    KnowledgeDocumentVersionModel.metadata_payload["category"].as_string()
                    == "product_care_sop",
                    KnowledgeDocumentVersionModel.metadata_payload["approval_status"].as_string()
                    == "approved",
                    func.json_array_length(
                        KnowledgeDocumentVersionModel.metadata_payload["approval_references"]
                    )
                    > 0,
                )
            )
            versions = rows.all()
        return any(
            _approved_care_version_supports_language(version.metadata_payload or {}, language)
            for version in versions
        )

    async def list_active_site_web_documents(
        self,
        *,
        tenant_id: str,
        site_id: str,
    ) -> tuple[ActiveWebDocumentSnapshot, ...]:
        async with self._session_factory() as session:
            rows = await session.execute(
                select(KnowledgeDocumentModel, KnowledgeDocumentVersionModel)
                .join(
                    KnowledgeDocumentVersionModel,
                    (KnowledgeDocumentVersionModel.tenant_id == KnowledgeDocumentModel.tenant_id)
                    & (
                        KnowledgeDocumentVersionModel.version_id
                        == KnowledgeDocumentModel.current_version_id
                    ),
                )
                .where(
                    KnowledgeDocumentModel.tenant_id == tenant_id,
                    KnowledgeDocumentModel.current_version_id.is_not(None),
                    KnowledgeDocumentVersionModel.status == "published",
                    KnowledgeDocumentVersionModel.index_status == "active",
                )
            )
        result: list[ActiveWebDocumentSnapshot] = []
        for document, version in rows:
            metadata = version.metadata_payload or {}
            if str(metadata.get("site_id") or "") != site_id:
                continue
            if str(metadata.get("source_type") or "") != "website_html":
                continue
            canonical_url = str(metadata.get("canonical_url") or document.source_path)
            product = metadata.get("product")
            product_identity = product if isinstance(product, dict) else {}
            product_key = str(
                product_identity.get("sku") or product_identity.get("mpn") or ""
            ).strip()
            result.append(
                ActiveWebDocumentSnapshot(
                    document_id=document.document_id,
                    version_id=version.version_id,
                    canonical_url=canonical_url,
                    requested_url=str(metadata.get("requested_url") or canonical_url),
                    final_url=str(metadata.get("final_url") or canonical_url),
                    etag=str(metadata.get("etag") or "") or None,
                    last_modified=str(metadata.get("last_modified") or "") or None,
                    product_key=product_key.upper() or None,
                    content_kind=str(metadata.get("content_kind") or "general"),
                    topics=tuple(str(value) for value in metadata.get("content_topics", [])),
                    activated_at=version.created_at.isoformat(),
                )
            )
        return tuple(result)

    async def list_active_site_version_ids(
        self,
        *,
        tenant_id: str,
        site_id: str,
    ) -> tuple[str, ...]:
        async with self._session_factory() as session:
            rows = await session.execute(
                select(KnowledgeDocumentModel, KnowledgeDocumentVersionModel)
                .join(
                    KnowledgeDocumentVersionModel,
                    (KnowledgeDocumentVersionModel.tenant_id == KnowledgeDocumentModel.tenant_id)
                    & (
                        KnowledgeDocumentVersionModel.version_id
                        == KnowledgeDocumentModel.current_version_id
                    ),
                )
                .where(
                    KnowledgeDocumentModel.tenant_id == tenant_id,
                    KnowledgeDocumentModel.current_version_id.is_not(None),
                    KnowledgeDocumentVersionModel.status == "published",
                    KnowledgeDocumentVersionModel.index_status == "active",
                )
            )
        return tuple(
            version.version_id
            for _document, version in rows
            if str((version.metadata_payload or {}).get("site_id") or "") == site_id
            and str((version.metadata_payload or {}).get("source_type") or "") == "website_html"
        )

    async def audit_site_publication(
        self,
        *,
        tenant_id: str,
        site_id: str,
        baseline_publication_id: str | None = None,
    ) -> KnowledgeControlPlaneSiteAudit:
        async with self._session_factory() as session:
            site = await session.scalar(
                select(SupportSiteModel).where(
                    SupportSiteModel.tenant_id == tenant_id,
                    SupportSiteModel.site_id == site_id,
                )
            )
            publication_query = select(WebSyncJobModel).where(
                WebSyncJobModel.tenant_id == tenant_id,
                WebSyncJobModel.site_id == site_id,
                WebSyncJobModel.mode == "production",
                WebSyncJobModel.status == "succeeded",
                WebSyncJobModel.publication_status == "published",
            )
            if baseline_publication_id:
                publication_query = publication_query.where(
                    WebSyncJobModel.job_id == baseline_publication_id
                )
            else:
                publication_query = publication_query.order_by(
                    WebSyncJobModel.completed_at.desc()
                ).limit(1)
            publication = await session.scalar(publication_query)
            if baseline_publication_id and publication is None:
                raise LookupError("the requested successful publication baseline was not found")

            resolved_publication_id = None if publication is None else publication.job_id
            baseline_version_ids: tuple[str, ...] = ()
            if resolved_publication_id is not None:
                baseline_version_ids = tuple(
                    dict.fromkeys(
                        str(value)
                        for value in await session.scalars(
                            select(WebSyncJobItemModel.version_id)
                            .where(
                                WebSyncJobItemModel.tenant_id == tenant_id,
                                WebSyncJobItemModel.job_id == resolved_publication_id,
                                WebSyncJobItemModel.version_id.is_not(None),
                            )
                            .order_by(WebSyncJobItemModel.ordinal)
                        )
                        if value
                    )
                )
            baseline_versions = await _versions_by_ids(
                session,
                tenant_id=tenant_id,
                version_ids=baseline_version_ids,
            )
            existing_baseline_ids = {item.version_id for item in baseline_versions}
            baseline_missing_version_ids = tuple(
                value for value in baseline_version_ids if value not in existing_baseline_ids
            )
            baseline_manifest_count = await _manifest_count(
                session,
                tenant_id=tenant_id,
                version_ids=baseline_version_ids,
            )

            current_rows = tuple(
                (
                    await session.execute(
                        select(KnowledgeDocumentModel, KnowledgeDocumentVersionModel)
                        .outerjoin(
                            KnowledgeDocumentVersionModel,
                            (
                                KnowledgeDocumentVersionModel.tenant_id
                                == KnowledgeDocumentModel.tenant_id
                            )
                            & (
                                KnowledgeDocumentVersionModel.version_id
                                == KnowledgeDocumentModel.current_version_id
                            ),
                        )
                        .where(
                            KnowledgeDocumentModel.tenant_id == tenant_id,
                            KnowledgeDocumentModel.current_version_id.is_not(None),
                        )
                    )
                ).all()
            )
            orphan_current_pointer_count = sum(
                version is None for _document, version in current_rows
            )
            site_current_rows = tuple(
                (document, version)
                for document, version in current_rows
                if version is not None
                and str((version.metadata_payload or {}).get("site_id") or "") == site_id
                and str((version.metadata_payload or {}).get("source_type") or "") == "website_html"
            )
            current_version_ids = tuple(
                dict.fromkeys(version.version_id for _document, version in site_current_rows)
            )
            current_manifest_count = await _manifest_count(
                session,
                tenant_id=tenant_id,
                version_ids=current_version_ids,
            )
            invalid_current_pointer_count = sum(
                version.status != "published" or version.index_status != "active"
                for _document, version in site_current_rows
            )
            discarded_current_pointer_count = sum(
                version.status == "discarded" or version.index_status == "discarded"
                for _document, version in site_current_rows
            )
            failed_current_pointer_count = sum(
                version.status == "failed" or version.index_status == "failed"
                for _document, version in site_current_rows
            )
            current_snapshot_ids = tuple(
                sorted(
                    {
                        str((version.metadata_payload or {}).get("snapshot_id") or "")
                        for _document, version in site_current_rows
                        if (version.metadata_payload or {}).get("snapshot_id")
                    }
                )
            )

            active_product_snapshots = tuple(
                (
                    await session.scalars(
                        select(ProductCatalogSnapshotModel)
                        .where(
                            ProductCatalogSnapshotModel.tenant_id == tenant_id,
                            ProductCatalogSnapshotModel.site_id == site_id,
                            ProductCatalogSnapshotModel.status == "active",
                        )
                        .order_by(ProductCatalogSnapshotModel.completed_at.desc())
                    )
                ).all()
            )
            active_product_snapshot = (
                active_product_snapshots[0] if active_product_snapshots else None
            )
            baseline_product_snapshot = None
            if resolved_publication_id is not None:
                baseline_product_snapshot = await session.scalar(
                    select(ProductCatalogSnapshotModel).where(
                        ProductCatalogSnapshotModel.tenant_id == tenant_id,
                        ProductCatalogSnapshotModel.site_id == site_id,
                        ProductCatalogSnapshotModel.snapshot_id == resolved_publication_id,
                    )
                )

        return KnowledgeControlPlaneSiteAudit(
            tenant_id=tenant_id,
            site_id=site_id,
            baseline_publication_id=resolved_publication_id,
            baseline_version_ids=baseline_version_ids,
            baseline_missing_version_ids=baseline_missing_version_ids,
            baseline_manifest_count=baseline_manifest_count,
            current_version_ids=current_version_ids,
            current_manifest_count=current_manifest_count,
            current_document_count=len(site_current_rows),
            invalid_current_pointer_count=invalid_current_pointer_count,
            discarded_current_pointer_count=discarded_current_pointer_count,
            failed_current_pointer_count=failed_current_pointer_count,
            orphan_current_pointer_count=orphan_current_pointer_count,
            current_snapshot_ids=current_snapshot_ids,
            active_product_snapshot_id=(
                None if active_product_snapshot is None else active_product_snapshot.snapshot_id
            ),
            active_product_snapshot_count=len(active_product_snapshots),
            active_product_count=(
                0 if active_product_snapshot is None else active_product_snapshot.active_count
            ),
            baseline_product_snapshot_status=(
                None if baseline_product_snapshot is None else baseline_product_snapshot.status
            ),
            baseline_product_count=(
                0 if baseline_product_snapshot is None else baseline_product_snapshot.active_count
            ),
            identifier_conflicts=_identifier_conflicts(baseline_versions),
            publication_state=("missing" if site is None else site.knowledge_publication_state),
            publication_active_id=(None if site is None else site.active_knowledge_publication_id),
            publication_pending_id=(
                None if site is None else site.pending_knowledge_publication_id
            ),
            publication_error_code=(None if site is None else site.knowledge_publication_error),
        )

    async def list_publication_version_ids(
        self,
        *,
        tenant_id: str,
        site_id: str,
        publication_id: str,
    ) -> tuple[str, ...]:
        """Return the immutable version manifest for a prior publication.

        Web sync jobs have an explicit item manifest. The fallback metadata
        query keeps legacy, non-worker syncs recoverable until those jobs are
        migrated to the worker manifest format.
        """
        async with self._session_factory() as session:
            item_ids = tuple(
                dict.fromkeys(
                    str(value)
                    for value in await session.scalars(
                        select(WebSyncJobItemModel.version_id)
                        .where(
                            WebSyncJobItemModel.tenant_id == tenant_id,
                            WebSyncJobItemModel.site_id == site_id,
                            WebSyncJobItemModel.job_id == publication_id,
                            WebSyncJobItemModel.version_id.is_not(None),
                        )
                        .order_by(WebSyncJobItemModel.ordinal)
                    )
                    if value
                )
            )
            if item_ids:
                return item_ids
            return tuple(
                dict.fromkeys(
                    str(value)
                    for value in await session.scalars(
                        select(KnowledgeDocumentVersionModel.version_id).where(
                            KnowledgeDocumentVersionModel.tenant_id == tenant_id,
                            KnowledgeDocumentVersionModel.metadata_payload["site_id"].as_string()
                            == site_id,
                            KnowledgeDocumentVersionModel.metadata_payload[
                                "source_type"
                            ].as_string()
                            == "website_html",
                            KnowledgeDocumentVersionModel.metadata_payload[
                                "snapshot_id"
                            ].as_string()
                            == publication_id,
                        )
                    )
                    if value
                )
            )

    async def get_site_publication_state(
        self,
        *,
        tenant_id: str,
        site_id: str,
    ) -> KnowledgeSitePublicationState:
        async with self._session_factory() as session:
            site = await session.scalar(
                select(SupportSiteModel).where(
                    SupportSiteModel.tenant_id == tenant_id,
                    SupportSiteModel.site_id == site_id,
                )
            )
        if site is None:
            return KnowledgeSitePublicationState(state="missing")
        return KnowledgeSitePublicationState(
            state=site.knowledge_publication_state,
            active_publication_id=site.active_knowledge_publication_id,
            pending_publication_id=site.pending_knowledge_publication_id,
            error_code=site.knowledge_publication_error,
            switch_origin_state=site.knowledge_publication_switch_origin,
        )

    async def begin_site_publication_switch(
        self,
        *,
        tenant_id: str,
        site_id: str,
        publication_id: str,
    ) -> KnowledgeSitePublicationState:
        publication_id = publication_id.strip()
        if not publication_id:
            raise ValueError("publication_id must not be empty")
        async with self._session_factory.begin() as session:
            site = await session.scalar(
                select(SupportSiteModel)
                .where(
                    SupportSiteModel.tenant_id == tenant_id,
                    SupportSiteModel.site_id == site_id,
                )
                .with_for_update()
            )
            if site is None:
                raise LookupError("support site does not exist")
            current_state = site.knowledge_publication_state
            if current_state == "switching":
                if site.pending_knowledge_publication_id == publication_id:
                    return KnowledgeSitePublicationState(
                        state=site.knowledge_publication_switch_origin or "active",
                        active_publication_id=site.active_knowledge_publication_id,
                        pending_publication_id=publication_id,
                        error_code=site.knowledge_publication_error,
                    )
                raise RuntimeError("another site publication switch is already in progress")
            if current_state not in {"active", "recovery_required"}:
                raise RuntimeError(
                    "site publication is not available for switching: " + current_state
                )
            previous_publication_id = site.active_knowledge_publication_id
            previous = KnowledgeSitePublicationState(
                state=current_state,
                active_publication_id=previous_publication_id,
                pending_publication_id=site.pending_knowledge_publication_id,
                error_code=site.knowledge_publication_error,
            )
            site.knowledge_publication_state = "switching"
            site.pending_knowledge_publication_id = publication_id
            site.knowledge_publication_error = None
            site.knowledge_publication_switch_origin = current_state
            site.updated_at = datetime.now(UTC)
            return previous

    async def complete_site_publication_switch(
        self,
        *,
        tenant_id: str,
        site_id: str,
        publication_id: str,
    ) -> None:
        async with self._session_factory.begin() as session:
            await _complete_site_publication_switch_in_session(
                session,
                tenant_id=tenant_id,
                site_id=site_id,
                publication_id=publication_id,
            )

    async def commit_site_publication(
        self,
        *,
        tenant_id: str,
        site_id: str,
        publication_id: str,
        version_ids: Sequence[str],
        discovered_count: int,
        indexed_count: int,
        error_summary: dict[str, str],
        completed_at: datetime,
    ) -> ProductSnapshotActivation:
        """Atomically expose one verified knowledge and product publication."""
        async with self._session_factory.begin() as session:
            await _activate_site_versions_in_session(
                session,
                tenant_id=tenant_id,
                site_id=site_id,
                version_ids=version_ids,
            )
            activation = await activate_product_snapshot_in_session(
                session,
                tenant_id=tenant_id,
                site_id=site_id,
                snapshot_id=publication_id,
                completed_at=completed_at,
            )
            await _complete_sync_in_session(
                session,
                tenant_id=tenant_id,
                sync_job_id=publication_id,
                discovered_count=discovered_count,
                indexed_count=indexed_count,
                failed_count=0,
                error_summary=error_summary,
                completed_at=completed_at,
            )
            await _complete_site_publication_switch_in_session(
                session,
                tenant_id=tenant_id,
                site_id=site_id,
                publication_id=publication_id,
            )
            return activation

    async def restore_site_publication(
        self,
        *,
        tenant_id: str,
        site_id: str,
        failed_publication_id: str,
        previous_publication_id: str | None,
        error_code: str,
    ) -> None:
        async with self._session_factory.begin() as session:
            site = await _get_support_site_for_update(session, tenant_id, site_id)
            if (
                site.knowledge_publication_state == "active"
                and site.active_knowledge_publication_id == previous_publication_id
                and site.pending_knowledge_publication_id is None
            ):
                return
            if site.knowledge_publication_state == "active":
                if (
                    site.active_knowledge_publication_id != failed_publication_id
                    or site.pending_knowledge_publication_id is not None
                ):
                    raise RuntimeError("site publication rollback does not match active state")
            elif (
                site.knowledge_publication_state not in {"switching", "recovery_required"}
                or site.pending_knowledge_publication_id != failed_publication_id
            ):
                raise RuntimeError("site publication rollback does not match pending state")
            site.knowledge_publication_state = "active"
            site.active_knowledge_publication_id = previous_publication_id
            site.pending_knowledge_publication_id = None
            site.knowledge_publication_error = error_code[:100]
            site.knowledge_publication_switch_origin = None
            site.updated_at = datetime.now(UTC)

    async def require_site_publication_recovery(
        self,
        *,
        tenant_id: str,
        site_id: str,
        publication_id: str,
        error_code: str,
    ) -> None:
        async with self._session_factory.begin() as session:
            site = await _get_support_site_for_update(session, tenant_id, site_id)
            if (
                site.knowledge_publication_state == "recovery_required"
                and site.pending_knowledge_publication_id == publication_id
                and site.knowledge_publication_error == error_code[:100]
            ):
                return
            if site.knowledge_publication_state == "active":
                site.pending_knowledge_publication_id = publication_id
            elif site.pending_knowledge_publication_id not in {None, publication_id}:
                raise RuntimeError("site publication recovery does not match pending state")
            site.knowledge_publication_state = "recovery_required"
            site.pending_knowledge_publication_id = publication_id
            site.knowledge_publication_error = error_code[:100]
            site.knowledge_publication_switch_origin = None
            site.updated_at = datetime.now(UTC)

    async def list_related_document_ids(
        self,
        *,
        tenant_id: str,
        document_ids: Sequence[str],
        limit: int = 20,
    ) -> tuple[str, ...]:
        if not document_ids:
            return ()
        async with self._session_factory() as session:
            values = list(
                await session.scalars(
                    select(KnowledgeLinkModel.resolved_document_id)
                    .where(
                        KnowledgeLinkModel.tenant_id == tenant_id,
                        KnowledgeLinkModel.source_document_id.in_(tuple(document_ids)),
                        KnowledgeLinkModel.resolved_document_id.is_not(None),
                    )
                    .limit(max(1, min(limit, 50)))
                )
            )
        return tuple(dict.fromkeys(str(value) for value in values if value))

    async def begin_sync(
        self,
        *,
        tenant_id: str,
        vault_path: str,
        actor_subject_id: str | None = None,
        correlation_id: str | None = None,
        approval_reference: str | None = None,
        sync_job_id: str | None = None,
    ) -> str:
        resolved_sync_job_id = sync_job_id or str(uuid4())
        now = datetime.now(UTC)
        async with self._session_factory.begin() as session:
            existing = await session.scalar(
                select(KnowledgeSyncJobModel).where(
                    KnowledgeSyncJobModel.tenant_id == tenant_id,
                    KnowledgeSyncJobModel.sync_job_id == resolved_sync_job_id,
                )
            )
            if existing is not None:
                if existing.vault_path != vault_path:
                    raise RuntimeError("knowledge sync job scope mismatch")
                return resolved_sync_job_id
            session.add(
                KnowledgeSyncJobModel(
                    tenant_id=tenant_id,
                    sync_job_id=resolved_sync_job_id,
                    vault_path=vault_path,
                    status="running",
                    started_at=now,
                )
            )
            session.add(
                AuditEventModel(
                    tenant_id=tenant_id,
                    event_id=str(uuid4()),
                    event_type="knowledge.sync.started",
                    resource_type="knowledge_sync_job",
                    resource_id=resolved_sync_job_id,
                    actor_subject_id=actor_subject_id,
                    correlation_id=correlation_id,
                    details={
                        "vault_path": vault_path,
                        **(
                            {"approval_reference": approval_reference}
                            if approval_reference is not None
                            else {}
                        ),
                    },
                    created_at=now,
                )
            )
        return resolved_sync_job_id

    async def count_indexed_chunks(
        self,
        *,
        tenant_id: str,
        version_ids: Sequence[str],
    ) -> int:
        unique_version_ids = tuple(dict.fromkeys(version_ids))
        if not unique_version_ids:
            return 0
        async with self._session_factory() as session:
            count = await session.scalar(
                select(func.count(KnowledgeChunkManifestModel.id)).where(
                    KnowledgeChunkManifestModel.tenant_id == tenant_id,
                    KnowledgeChunkManifestModel.version_id.in_(unique_version_ids),
                    KnowledgeChunkManifestModel.status.in_(("indexed", "active")),
                    KnowledgeChunkManifestModel.qdrant_point_id.is_not(None),
                )
            )
        return int(count or 0)

    async def is_reusable_site_version(
        self,
        *,
        tenant_id: str,
        site_id: str,
        version_id: str,
        publication_id: str,
    ) -> bool:
        """Return whether a version is safe to use as a 304/baseline reference."""
        async with self._session_factory() as session:
            row = await session.execute(
                select(KnowledgeDocumentModel, KnowledgeDocumentVersionModel)
                .join(
                    KnowledgeDocumentVersionModel,
                    (KnowledgeDocumentVersionModel.tenant_id == KnowledgeDocumentModel.tenant_id)
                    & (
                        KnowledgeDocumentVersionModel.document_id
                        == KnowledgeDocumentModel.document_id
                    ),
                )
                .where(
                    KnowledgeDocumentModel.tenant_id == tenant_id,
                    KnowledgeDocumentVersionModel.version_id == version_id,
                )
            )
            result = row.first()
            if result is None:
                return False
            document, version = result
            metadata = version.metadata_payload or {}
            if metadata.get("site_id") != site_id:
                return False
            if metadata.get("source_type") != "website_html":
                return False
            if version.status != "published" or version.index_status not in {"indexed", "active"}:
                return False
            if version.lifecycle_state not in {"publishable", "active"} and not (
                version.lifecycle_state == "draft" and version.snapshot_id is None
            ):
                return False
            snapshot_id = version.snapshot_id or metadata.get("snapshot_id")
            if snapshot_id == publication_id:
                return True
            # A baseline is reusable only while its owning publication is still
            # successful. This prevents canceled snapshots from leaking into a
            # later job through an old validator.
            baseline = None
            if snapshot_id:
                baseline = await session.scalar(
                    select(WebSyncJobModel).where(
                        WebSyncJobModel.tenant_id == tenant_id,
                        WebSyncJobModel.job_id == snapshot_id,
                    )
                )
            return (
                document.current_version_id == version.version_id
                and version.index_status == "active"
                and baseline is not None
                and baseline.status == "succeeded"
                and baseline.publication_status == "published"
            )

    async def validate_site_publication_versions(
        self,
        *,
        tenant_id: str,
        site_id: str,
        publication_id: str,
        version_ids: Sequence[str],
    ) -> tuple[str, ...]:
        """Validate target versions before the publication transaction starts."""
        invalid_ids = await self.invalid_site_publication_version_ids(
            tenant_id=tenant_id,
            site_id=site_id,
            publication_id=publication_id,
            version_ids=version_ids,
        )
        if not invalid_ids:
            return ()
        async with self._session_factory() as session:
            versions = {
                version.version_id: version
                for version in (
                    await session.scalars(
                        select(KnowledgeDocumentVersionModel).where(
                            KnowledgeDocumentVersionModel.tenant_id == tenant_id,
                            KnowledgeDocumentVersionModel.version_id.in_(invalid_ids),
                        )
                    )
                ).all()
            }
        issues: list[str] = []
        for version_id in invalid_ids:
            version = versions.get(version_id)
            if version is None:
                issues.append(f"version_id={version_id},actual_state=missing")
                continue
            metadata = version.metadata_payload or {}
            actual_snapshot = str(version.snapshot_id or metadata.get("snapshot_id") or "none")
            issues.append(
                f"version_id={version_id},snapshot_id={actual_snapshot},"
                f"status={version.status},index_status={version.index_status},"
                f"lifecycle_state={version.lifecycle_state}"
            )
        return tuple(issues)

    async def invalid_site_publication_version_ids(
        self,
        *,
        tenant_id: str,
        site_id: str,
        publication_id: str,
        version_ids: Sequence[str],
    ) -> tuple[str, ...]:
        """Return missing, discarded, foreign, or invalid baseline version IDs."""
        target_ids = tuple(dict.fromkeys(version_ids))
        if not target_ids:
            return ()
        async with self._session_factory() as session:
            rows = tuple(
                (
                    await session.execute(
                        select(KnowledgeDocumentModel, KnowledgeDocumentVersionModel)
                        .join(
                            KnowledgeDocumentVersionModel,
                            (
                                KnowledgeDocumentVersionModel.tenant_id
                                == KnowledgeDocumentModel.tenant_id
                            )
                            & (
                                KnowledgeDocumentVersionModel.document_id
                                == KnowledgeDocumentModel.document_id
                            ),
                        )
                        .where(
                            KnowledgeDocumentModel.tenant_id == tenant_id,
                            KnowledgeDocumentVersionModel.version_id.in_(target_ids),
                        )
                    )
                ).all()
            )
            baseline_ids = {
                str(version.snapshot_id or (version.metadata_payload or {}).get("snapshot_id"))
                for _document, version in rows
                if version.snapshot_id or (version.metadata_payload or {}).get("snapshot_id")
            }
            baselines = {
                job.job_id: job
                for job in (
                    await session.scalars(
                        select(WebSyncJobModel).where(
                            WebSyncJobModel.tenant_id == tenant_id,
                            WebSyncJobModel.job_id.in_(baseline_ids),
                        )
                    )
                ).all()
            }
        valid: set[str] = set()
        for document, version in rows:
            metadata = version.metadata_payload or {}
            if metadata.get("site_id") != site_id or metadata.get("source_type") != "website_html":
                continue
            lifecycle_ready = version.lifecycle_state in {"publishable", "active"} or (
                version.lifecycle_state == "draft" and version.snapshot_id is None
            )
            if (
                version.status != "published"
                or version.index_status not in {"indexed", "active"}
                or not lifecycle_ready
            ):
                continue
            snapshot_id = str(version.snapshot_id or metadata.get("snapshot_id") or "")
            if snapshot_id == publication_id:
                valid.add(version.version_id)
                continue
            baseline = baselines.get(snapshot_id)
            if (
                document.current_version_id == version.version_id
                and version.index_status == "active"
                and baseline is not None
                and baseline.status == "succeeded"
                and baseline.publication_status == "published"
            ):
                valid.add(version.version_id)
        return tuple(version_id for version_id in target_ids if version_id not in valid)

    async def get_latest_version(
        self, *, tenant_id: str, document_id: str
    ) -> KnowledgeVersionSnapshot | None:
        async with self._session_factory() as session:
            document = await session.scalar(
                select(KnowledgeDocumentModel).where(
                    KnowledgeDocumentModel.tenant_id == tenant_id,
                    KnowledgeDocumentModel.document_id == document_id,
                )
            )
            if document is None or document.current_version_id is None:
                return None
            version = await session.scalar(
                select(KnowledgeDocumentVersionModel).where(
                    KnowledgeDocumentVersionModel.tenant_id == tenant_id,
                    KnowledgeDocumentVersionModel.version_id == document.current_version_id,
                )
            )
            if version is None:
                return None
            return KnowledgeVersionSnapshot(
                version_id=version.version_id,
                content_hash=version.content_hash,
                status=version.status,
                index_status=version.index_status,
                index_namespace=str(version.metadata_payload.get("index_namespace") or "") or None,
                lifecycle_state=version.lifecycle_state,
            )

    async def stage_version(
        self,
        *,
        draft: KnowledgeVersionDraft,
        chunks: Sequence[KnowledgeChunkRecord],
    ) -> str:
        validate_document_metadata(
            title=draft.title,
            document_id=draft.document_id,
            url=draft.source_path,
        )
        for chunk in chunks:
            validate_chunk_metadata(
                heading=chunk.heading,
                chunk_type=chunk.chunk_type,
                parent_chunk_id=chunk.parent_chunk_id,
                section_path=chunk.section_path,
                fact_keys=chunk.fact_keys,
                document_id=draft.document_id,
                url=draft.source_path,
            )
        async with self._session_factory.begin() as session:
            existing = await session.scalar(
                select(KnowledgeDocumentVersionModel).where(
                    KnowledgeDocumentVersionModel.tenant_id == draft.tenant_id,
                    KnowledgeDocumentVersionModel.version_id == draft.version_id,
                )
            )
            document = await session.scalar(
                select(KnowledgeDocumentModel).where(
                    KnowledgeDocumentModel.tenant_id == draft.tenant_id,
                    KnowledgeDocumentModel.document_id == draft.document_id,
                )
            )
            if existing is not None:
                snapshot_id = str(draft.metadata.get("snapshot_id") or "")
                reusable_existing = (
                    existing.status != "discarded"
                    and existing.index_status != "discarded"
                    and existing.lifecycle_state != "discarded"
                    and (
                        existing.snapshot_id == snapshot_id
                        or (
                            document is not None
                            and document.current_version_id == existing.version_id
                            and existing.index_status == "active"
                        )
                    )
                )
                if reusable_existing:
                    return existing.version_id
                # Deterministic content IDs are useful for idempotency, but a
                # discarded attempt must never be resurrected. Materialize a
                # fresh version scoped to this snapshot instead.
                # A discarded attempt must never be resurrected. Each retry
                # receives a fresh ID so cleanup cannot poison the next attempt.
                draft_version_id = str(uuid4())
                draft = KnowledgeVersionDraft(
                    version_id=draft_version_id,
                    tenant_id=draft.tenant_id,
                    document_id=draft.document_id,
                    source_path=draft.source_path,
                    title=draft.title,
                    version=draft.version,
                    content_hash=draft.content_hash,
                    status=draft.status,
                    authority_level=draft.authority_level,
                    priority=draft.priority,
                    language=draft.language,
                    audience=draft.audience,
                    effective_from=draft.effective_from,
                    effective_to=draft.effective_to,
                    metadata=draft.metadata,
                    internal_links=draft.internal_links,
                )

            now = datetime.now(UTC)
            if document is None:
                document = KnowledgeDocumentModel(
                    tenant_id=draft.tenant_id,
                    document_id=draft.document_id,
                    source_path=draft.source_path,
                    title=draft.title,
                    status=draft.status,
                    current_version_id=None,
                    created_at=now,
                    updated_at=now,
                )
                session.add(document)
                await session.flush()
            else:
                document.source_path = draft.source_path
                document.title = draft.title
                document.updated_at = now

            session.add(
                KnowledgeDocumentVersionModel(
                    tenant_id=draft.tenant_id,
                    version_id=draft.version_id,
                    document_id=draft.document_id,
                    version=draft.version,
                    content_hash=draft.content_hash,
                    status=draft.status,
                    snapshot_id=str(draft.metadata.get("snapshot_id") or "") or None,
                    lifecycle_state="staged",
                    index_status="pending",
                    authority_level=draft.authority_level,
                    priority=draft.priority,
                    language=draft.language,
                    audience=draft.audience,
                    effective_from=_parse_datetime(draft.effective_from),
                    effective_to=_parse_datetime(draft.effective_to),
                    metadata_payload=draft.metadata,
                    created_at=now,
                )
            )
            try:
                await session.flush()
            except IntegrityError as exc:
                if _is_knowledge_version_identity_violation(exc):
                    raise KnowledgeVersionSchemaInvariantError(
                        "knowledge version uniqueness invariant violation; schema or staging "
                        "remediation is required"
                    ) from exc
                raise

            chunk_manifests = [
                KnowledgeChunkManifestModel(
                    tenant_id=draft.tenant_id,
                    chunk_id=chunk.chunk_id,
                    version_id=draft.version_id,
                    document_id=draft.document_id,
                    content_hash=chunk.content_hash,
                    sequence=chunk.sequence,
                    heading=chunk.heading,
                    chunk_type=chunk.chunk_type,
                    parent_chunk_id=chunk.parent_chunk_id,
                    section_path=list(chunk.section_path),
                    fact_keys=list(chunk.fact_keys),
                    status="pending",
                    created_at=now,
                )
                for chunk in chunks
            ]
            knowledge_links: list[KnowledgeLinkModel] = []
            for reference in draft.internal_links:
                with session.no_autoflush:
                    resolved = await session.scalar(
                        select(KnowledgeDocumentModel).where(
                            KnowledgeDocumentModel.tenant_id == draft.tenant_id,
                            or_(
                                KnowledgeDocumentModel.document_id == reference,
                                KnowledgeDocumentModel.title == reference,
                            ),
                        )
                    )
                knowledge_links.append(
                    KnowledgeLinkModel(
                        tenant_id=draft.tenant_id,
                        link_id=str(
                            uuid5(
                                NAMESPACE_URL,
                                f"{draft.tenant_id}:{draft.version_id}:{reference}",
                            )
                        ),
                        version_id=draft.version_id,
                        source_document_id=draft.document_id,
                        target_reference=reference,
                        resolved_document_id=(None if resolved is None else resolved.document_id),
                        relation_type="references",
                        relation_metadata={},
                        created_at=now,
                    )
                )
            session.add_all(chunk_manifests)
            session.add_all(knowledge_links)
            session.add(
                AuditEventModel(
                    tenant_id=draft.tenant_id,
                    event_id=str(uuid4()),
                    event_type="knowledge.version.staged",
                    resource_type="knowledge_document_version",
                    resource_id=draft.version_id,
                    details={
                        "document_id": draft.document_id,
                        "status": draft.status,
                        "chunk_count": len(chunks),
                    },
                    created_at=now,
                )
            )
            await session.flush()
        return draft.version_id

    async def mark_indexed(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
        point_ids: Sequence[str],
        index_namespace: str,
        activate: bool = True,
    ) -> None:
        async with self._session_factory.begin() as session:
            version = await _get_version(session, tenant_id, version_id)
            version.index_status = "active" if activate else "indexed"
            version.lifecycle_state = "active" if activate else "publishable"
            version.metadata_payload = {
                **(version.metadata_payload or {}),
                "index_namespace": index_namespace,
            }
            if activate:
                document = await _get_document(session, tenant_id, document_id)
                document.status = "published"
                document.current_version_id = version_id
            manifests = list(
                (
                    await session.scalars(
                        select(KnowledgeChunkManifestModel)
                        .where(
                            KnowledgeChunkManifestModel.tenant_id == tenant_id,
                            KnowledgeChunkManifestModel.version_id == version_id,
                        )
                        .order_by(KnowledgeChunkManifestModel.sequence)
                    )
                ).all()
            )
            if len(manifests) != len(point_ids):
                raise RuntimeError("chunk manifest count does not match Qdrant points")
            for manifest, point_id in zip(manifests, point_ids, strict=True):
                manifest.qdrant_point_id = point_id
                manifest.status = "active" if activate else "indexed"

    async def activate_document_version(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str | None,
    ) -> None:
        async with self._session_factory.begin() as session:
            document = await session.scalar(
                select(KnowledgeDocumentModel)
                .where(
                    KnowledgeDocumentModel.tenant_id == tenant_id,
                    KnowledgeDocumentModel.document_id == document_id,
                )
                .with_for_update()
            )
            if document is None:
                raise RuntimeError("knowledge document not found")
            current_version = None
            if document.current_version_id:
                current_version = await session.scalar(
                    select(KnowledgeDocumentVersionModel).where(
                        KnowledgeDocumentVersionModel.tenant_id == tenant_id,
                        KnowledgeDocumentVersionModel.version_id == document.current_version_id,
                    )
                )
            if current_version is not None and current_version.version_id != version_id:
                current_version.index_status = "indexed"
                current_version.lifecycle_state = "publishable"
                current_manifests = await session.scalars(
                    select(KnowledgeChunkManifestModel).where(
                        KnowledgeChunkManifestModel.tenant_id == tenant_id,
                        KnowledgeChunkManifestModel.version_id == current_version.version_id,
                    )
                )
                for manifest in current_manifests:
                    manifest.status = "indexed"
            if version_id is None:
                document.current_version_id = None
                document.status = "superseded"
                document.updated_at = datetime.now(UTC)
                return
            version = await _get_version(session, tenant_id, version_id)
            if version.document_id != document_id:
                raise RuntimeError("knowledge activation document scope mismatch")
            if version.index_status not in {"indexed", "active"}:
                raise RuntimeError("knowledge version must be indexed before activation")
            manifests = list(
                await session.scalars(
                    select(KnowledgeChunkManifestModel).where(
                        KnowledgeChunkManifestModel.tenant_id == tenant_id,
                        KnowledgeChunkManifestModel.version_id == version_id,
                    )
                )
            )
            if any(manifest.qdrant_point_id is None for manifest in manifests):
                raise RuntimeError("knowledge version manifests are not fully indexed")
            version.index_status = "active"
            version.lifecycle_state = "active"
            for manifest in manifests:
                manifest.status = "active"
            document.current_version_id = version_id
            document.status = "published"
            document.updated_at = datetime.now(UTC)

    async def activate_site_versions(
        self,
        *,
        tenant_id: str,
        site_id: str,
        version_ids: Sequence[str],
    ) -> None:
        async with self._session_factory.begin() as session:
            await _activate_site_versions_in_session(
                session,
                tenant_id=tenant_id,
                site_id=site_id,
                version_ids=version_ids,
            )

    async def mark_excluded(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
    ) -> None:
        async with self._session_factory.begin() as session:
            version = await _get_version(session, tenant_id, version_id)
            version.index_status = "excluded"
            version.lifecycle_state = "discarded"
            document = await _get_document(session, tenant_id, document_id)
            document.status = version.status
            document.current_version_id = version_id
            manifests = await session.scalars(
                select(KnowledgeChunkManifestModel).where(
                    KnowledgeChunkManifestModel.tenant_id == tenant_id,
                    KnowledgeChunkManifestModel.version_id == version_id,
                )
            )
            for manifest in manifests:
                manifest.status = "excluded"

    async def record_ingestion_rejection(
        self,
        *,
        tenant_id: str,
        sync_job_id: str,
        source_path: str,
        reason_code: str,
    ) -> None:
        async with self._session_factory.begin() as session:
            session.add(
                AuditEventModel(
                    tenant_id=tenant_id,
                    event_id=str(uuid4()),
                    event_type="knowledge.document.quarantined",
                    resource_type="knowledge_sync_job",
                    resource_id=sync_job_id,
                    details={"source_path": source_path, "reason_code": reason_code},
                    created_at=datetime.now(UTC),
                )
            )

    async def record_conflict(
        self,
        *,
        tenant_id: str,
        question: str,
        version_ids: Sequence[str],
        risk_level: int,
    ) -> str:
        fingerprint_source = ":".join((tenant_id, question, *sorted(version_ids)))
        fingerprint = sha256(fingerprint_source.encode("utf-8")).hexdigest()
        async with self._session_factory.begin() as session:
            existing = await session.scalar(
                select(KnowledgeConflictModel).where(
                    KnowledgeConflictModel.tenant_id == tenant_id,
                    KnowledgeConflictModel.conflict_fingerprint == fingerprint,
                )
            )
            if existing is not None:
                return existing.conflict_id
            conflict_id = str(uuid4())
            session.add(
                KnowledgeConflictModel(
                    tenant_id=tenant_id,
                    conflict_id=conflict_id,
                    conflict_fingerprint=fingerprint,
                    status="open",
                    risk_level=risk_level,
                    question_key=question[:500],
                    affected_version_ids=list(sorted(version_ids)),
                    created_at=datetime.now(UTC),
                )
            )
            return conflict_id

    async def complete_sync(
        self,
        *,
        tenant_id: str,
        sync_job_id: str,
        discovered_count: int,
        indexed_count: int,
        failed_count: int,
        error_summary: dict[str, str],
    ) -> None:
        async with self._session_factory.begin() as session:
            await _complete_sync_in_session(
                session,
                tenant_id=tenant_id,
                sync_job_id=sync_job_id,
                discovered_count=discovered_count,
                indexed_count=indexed_count,
                failed_count=failed_count,
                error_summary=error_summary,
                completed_at=datetime.now(UTC),
            )

    async def fail_sync(
        self,
        *,
        tenant_id: str,
        sync_job_id: str,
        error_code: str,
    ) -> None:
        async with self._session_factory.begin() as session:
            job = await _get_job(session, tenant_id, sync_job_id)
            job.status = "failed"
            job.failed_count = max(1, job.failed_count)
            job.error_summary = {"fatal": error_code}
            job.completed_at = datetime.now(UTC)

    async def discard_site_snapshot_versions(
        self,
        *,
        tenant_id: str,
        site_id: str,
        snapshot_id: str,
    ) -> int:
        async with self._session_factory.begin() as session:
            versions = tuple(
                (
                    await session.scalars(
                        select(KnowledgeDocumentVersionModel).where(
                            KnowledgeDocumentVersionModel.tenant_id == tenant_id,
                            KnowledgeDocumentVersionModel.metadata_payload["site_id"].as_string()
                            == site_id,
                            KnowledgeDocumentVersionModel.metadata_payload[
                                "snapshot_id"
                            ].as_string()
                            == snapshot_id,
                        )
                    )
                ).all()
            )
            if not versions:
                return 0
            # Publication can fail after the target snapshot was activated.
            # The snapshot scope above is authoritative, so rollback must also
            # discard versions briefly marked active by that same publication.
            version_ids = tuple(item.version_id for item in versions)
            current_documents = tuple(
                (
                    await session.scalars(
                        select(KnowledgeDocumentModel).where(
                            KnowledgeDocumentModel.tenant_id == tenant_id,
                            KnowledgeDocumentModel.current_version_id.in_(version_ids),
                        )
                    )
                ).all()
            )
            for document in current_documents:
                document.current_version_id = None
                document.status = "superseded"
                document.updated_at = datetime.now(UTC)
            for version in versions:
                version.status = "discarded"
                version.index_status = "discarded"
                version.lifecycle_state = "discarded"
            manifests = tuple(
                (
                    await session.scalars(
                        select(KnowledgeChunkManifestModel).where(
                            KnowledgeChunkManifestModel.tenant_id == tenant_id,
                            KnowledgeChunkManifestModel.version_id.in_(version_ids),
                        )
                    )
                ).all()
            )
            for manifest in manifests:
                manifest.status = "discarded"
                manifest.qdrant_point_id = None
            await _invalidate_job_version_references(
                session,
                tenant_id=tenant_id,
                version_ids=version_ids,
            )
            return len(versions)

    async def discard_staged_document(
        self,
        *,
        tenant_id: str,
        site_id: str,
        snapshot_id: str,
        document_id: str,
    ) -> int:
        async with self._session_factory.begin() as session:
            versions = tuple(
                (
                    await session.scalars(
                        select(KnowledgeDocumentVersionModel).where(
                            KnowledgeDocumentVersionModel.tenant_id == tenant_id,
                            KnowledgeDocumentVersionModel.document_id == document_id,
                            KnowledgeDocumentVersionModel.metadata_payload["site_id"].as_string()
                            == site_id,
                            KnowledgeDocumentVersionModel.metadata_payload[
                                "snapshot_id"
                            ].as_string()
                            == snapshot_id,
                            KnowledgeDocumentVersionModel.index_status.in_(
                                ("pending", "staged", "indexing")
                            ),
                        )
                    )
                ).all()
            )
            if not versions:
                return 0
            versions = tuple(item for item in versions if item.index_status != "active")
            if not versions:
                return 0
            version_ids = tuple(item.version_id for item in versions)
            current = tuple(
                (
                    await session.scalars(
                        select(KnowledgeDocumentModel).where(
                            KnowledgeDocumentModel.tenant_id == tenant_id,
                            KnowledgeDocumentModel.document_id == document_id,
                            KnowledgeDocumentModel.current_version_id.in_(version_ids),
                        )
                    )
                ).all()
            )
            for document in current:
                document.current_version_id = None
                document.status = "superseded"
                document.updated_at = datetime.now(UTC)
            for version in versions:
                version.status = "discarded"
                version.index_status = "discarded"
                version.lifecycle_state = "discarded"
            manifests = tuple(
                (
                    await session.scalars(
                        select(KnowledgeChunkManifestModel).where(
                            KnowledgeChunkManifestModel.tenant_id == tenant_id,
                            KnowledgeChunkManifestModel.version_id.in_(version_ids),
                        )
                    )
                ).all()
            )
            for manifest in manifests:
                manifest.status = "discarded"
                manifest.qdrant_point_id = None
            await _invalidate_job_version_references(
                session,
                tenant_id=tenant_id,
                version_ids=version_ids,
            )
            return len(versions)


async def _versions_by_ids(
    session: AsyncSession,
    *,
    tenant_id: str,
    version_ids: Sequence[str],
) -> tuple[KnowledgeDocumentVersionModel, ...]:
    if not version_ids:
        return ()
    return tuple(
        (
            await session.scalars(
                select(KnowledgeDocumentVersionModel).where(
                    KnowledgeDocumentVersionModel.tenant_id == tenant_id,
                    KnowledgeDocumentVersionModel.version_id.in_(tuple(version_ids)),
                )
            )
        ).all()
    )


async def _invalidate_job_version_references(
    session: AsyncSession,
    *,
    tenant_id: str,
    version_ids: Sequence[str],
) -> None:
    """Make discarded versions impossible to appear as successful job output."""
    if not version_ids:
        return
    items = tuple(
        (
            await session.scalars(
                select(WebSyncJobItemModel)
                .where(
                    WebSyncJobItemModel.tenant_id == tenant_id,
                    WebSyncJobItemModel.version_id.in_(tuple(version_ids)),
                    WebSyncJobItemModel.status.in_(("succeeded", "not_modified")),
                )
                .with_for_update()
            )
        ).all()
    )
    if not items:
        return
    job_ids = tuple(dict.fromkeys(item.job_id for item in items))
    jobs = {
        job.job_id: job
        for job in (
            await session.scalars(
                select(WebSyncJobModel)
                .where(
                    WebSyncJobModel.tenant_id == tenant_id,
                    WebSyncJobModel.job_id.in_(job_ids),
                )
                .with_for_update()
            )
        ).all()
    }
    for item in items:
        job = jobs.get(item.job_id)
        if job is not None:
            job.completed_count = max(0, job.completed_count - 1)
            if item.status == "succeeded":
                job.succeeded_count = max(0, job.succeeded_count - 1)
            else:
                job.not_modified_count = max(0, job.not_modified_count - 1)
        item.status = "pending"
        item.version_id = None
        item.etag = None
        item.last_modified = None
        item.completed_at = None
        item.report_payload = {}
        item.error_code = "stale_version_reference"
        item.error_message = "referenced knowledge version was discarded; reconciliation required"
        item.outcome_reason = "reconciliation_required"
        if job is not None and job.status in {"succeeded", "completed"}:
            job.status = "blocked"
            job.phase = "awaiting_remediation"
            job.publication_status = "pending"
            job.error_code = "stale_version_reference"
            job.error_message = "a discarded knowledge version was referenced by a successful item"
            report_payload = dict(job.report_payload or {})
            reasons = list(report_payload.get("publication_block_reasons", []))
            if "stale_version_reference" not in reasons:
                reasons.append("stale_version_reference")
            report_payload["published"] = False
            report_payload["publication_block_reasons"] = reasons
            report_payload["blocking_issue_count"] = max(
                1, int(report_payload.get("blocking_issue_count", 0))
            )
            report_payload["errors"] = {
                **dict(report_payload.get("errors", {})),
                "stale_version_reference": job.error_message,
            }
            job.report_payload = report_payload
        if job is not None:
            session.add(
                AuditEventModel(
                    tenant_id=tenant_id,
                    event_id=str(uuid4()),
                    event_type="knowledge.web_sync.stale_version_reference_invalidated",
                    actor_subject_id=None,
                    correlation_id=None,
                    trace_id=None,
                    resource_type="web_sync_job",
                    resource_id=job.job_id,
                    details={
                        "version_ids": list(dict.fromkeys(version_ids))[:20],
                        "item_id": item.item_id,
                        "outcome_reason": "reconciliation_required",
                    },
                    created_at=datetime.now(UTC),
                )
            )


async def _get_support_site_for_update(
    session: AsyncSession,
    tenant_id: str,
    site_id: str,
) -> SupportSiteModel:
    site = await session.scalar(
        select(SupportSiteModel)
        .where(
            SupportSiteModel.tenant_id == tenant_id,
            SupportSiteModel.site_id == site_id,
        )
        .with_for_update()
    )
    if site is None:
        raise LookupError("support site does not exist")
    return site


async def _manifest_count(
    session: AsyncSession,
    *,
    tenant_id: str,
    version_ids: Sequence[str],
) -> int:
    if not version_ids:
        return 0
    return int(
        await session.scalar(
            select(func.count(KnowledgeChunkManifestModel.id)).where(
                KnowledgeChunkManifestModel.tenant_id == tenant_id,
                KnowledgeChunkManifestModel.version_id.in_(tuple(version_ids)),
                KnowledgeChunkManifestModel.qdrant_point_id.is_not(None),
            )
        )
        or 0
    )


def _identifier_conflicts(
    versions: Sequence[KnowledgeDocumentVersionModel],
) -> tuple[ProductIdentifierConflict, ...]:
    groups: dict[str, dict[str, set[str]]] = {}
    for version in versions:
        metadata = version.metadata_payload or {}
        product = metadata.get("product")
        if not isinstance(product, dict):
            continue
        identifiers = {
            str(value).strip().upper()
            for value in (product.get("sku"), product.get("mpn"))
            if str(value or "").strip()
        }
        for identifier in identifiers:
            group = groups.setdefault(
                identifier,
                {
                    "document_ids": set(),
                    "version_ids": set(),
                    "canonical_urls": set(),
                    "product_names": set(),
                    "brands": set(),
                },
            )
            group["document_ids"].add(version.document_id)
            group["version_ids"].add(version.version_id)
            for key, destination in (
                ("canonical_url", "canonical_urls"),
                ("name", "product_names"),
                ("brand", "brands"),
            ):
                value = metadata.get(key) if key == "canonical_url" else product.get(key)
                if str(value or "").strip():
                    group[destination].add(str(value).strip())

    conflicts: list[ProductIdentifierConflict] = []
    for identifier, values in sorted(groups.items()):
        if len(values["document_ids"]) < 2 and len(values["canonical_urls"]) < 2:
            continue
        conflicts.append(
            ProductIdentifierConflict(
                identifier=identifier,
                document_ids=tuple(sorted(values["document_ids"])),
                version_ids=tuple(sorted(values["version_ids"])),
                canonical_urls=tuple(sorted(values["canonical_urls"])),
                product_names=tuple(sorted(values["product_names"])),
                brands=tuple(sorted(values["brands"])),
            )
        )
    return tuple(conflicts)


async def _activate_site_versions_in_session(
    session: AsyncSession,
    *,
    tenant_id: str,
    site_id: str,
    version_ids: Sequence[str],
) -> None:
    target_version_ids = tuple(dict.fromkeys(version_ids))
    current_rows = await session.execute(
        select(KnowledgeDocumentModel, KnowledgeDocumentVersionModel)
        .join(
            KnowledgeDocumentVersionModel,
            (KnowledgeDocumentVersionModel.tenant_id == KnowledgeDocumentModel.tenant_id)
            & (
                KnowledgeDocumentVersionModel.version_id
                == KnowledgeDocumentModel.current_version_id
            ),
        )
        .where(
            KnowledgeDocumentModel.tenant_id == tenant_id,
            KnowledgeDocumentModel.current_version_id.is_not(None),
        )
        .with_for_update(of=KnowledgeDocumentModel)
    )
    desired = set(target_version_ids)
    for document, current_version in current_rows:
        metadata = current_version.metadata_payload or {}
        if (
            metadata.get("site_id") == site_id
            and metadata.get("source_type") == "website_html"
            and current_version.version_id not in desired
        ):
            document.current_version_id = None
            document.status = "superseded"
            current_version.index_status = "indexed"
            current_version.lifecycle_state = "publishable"
    if not target_version_ids:
        return
    versions = list(
        await session.scalars(
            select(KnowledgeDocumentVersionModel)
            .where(
                KnowledgeDocumentVersionModel.tenant_id == tenant_id,
                KnowledgeDocumentVersionModel.version_id.in_(target_version_ids),
            )
            .with_for_update()
        )
    )
    if len(versions) != len(target_version_ids):
        raise RuntimeError("site activation references missing knowledge versions")
    activated_at = datetime.now(UTC)
    for version in versions:
        metadata = version.metadata_payload or {}
        if metadata.get("site_id") != site_id or metadata.get("source_type") != "website_html":
            raise RuntimeError("site activation scope mismatch")
        lifecycle_ready = version.lifecycle_state in {"publishable", "active"} or (
            version.lifecycle_state == "draft" and version.snapshot_id is None
        )
        if version.status != "published" or not lifecycle_ready:
            raise RuntimeError("site activation requires published knowledge versions")
        if version.index_status not in {"indexed", "active"}:
            raise RuntimeError("site activation requires indexed knowledge versions")
        document = await _get_document(session, tenant_id, version.document_id)
        document.current_version_id = version.version_id
        document.status = "published"
        document.updated_at = activated_at
        version.index_status = "active"
        version.lifecycle_state = "active"


async def _complete_sync_in_session(
    session: AsyncSession,
    *,
    tenant_id: str,
    sync_job_id: str,
    discovered_count: int,
    indexed_count: int,
    failed_count: int,
    error_summary: dict[str, str],
    completed_at: datetime,
) -> None:
    job = await _get_job(session, tenant_id, sync_job_id)
    job.status = (
        "completed_with_errors"
        if failed_count
        else "completed_with_warnings"
        if error_summary
        else "completed"
    )
    job.discovered_count = discovered_count
    job.indexed_count = indexed_count
    job.failed_count = failed_count
    job.error_summary = error_summary
    job.completed_at = completed_at


async def _complete_site_publication_switch_in_session(
    session: AsyncSession,
    *,
    tenant_id: str,
    site_id: str,
    publication_id: str,
) -> None:
    site = await _get_support_site_for_update(session, tenant_id, site_id)
    if (
        site.knowledge_publication_state == "active"
        and site.active_knowledge_publication_id == publication_id
        and site.pending_knowledge_publication_id is None
    ):
        return
    if (
        site.knowledge_publication_state != "switching"
        or site.pending_knowledge_publication_id != publication_id
    ):
        raise RuntimeError("site publication switch completion does not match pending state")
    site.knowledge_publication_state = "active"
    site.active_knowledge_publication_id = publication_id
    site.pending_knowledge_publication_id = None
    site.knowledge_publication_error = None
    site.knowledge_publication_switch_origin = None
    site.updated_at = datetime.now(UTC)


async def _get_document(
    session: AsyncSession, tenant_id: str, document_id: str
) -> KnowledgeDocumentModel:
    document = await session.scalar(
        select(KnowledgeDocumentModel).where(
            KnowledgeDocumentModel.tenant_id == tenant_id,
            KnowledgeDocumentModel.document_id == document_id,
        )
    )
    if document is None:
        raise RuntimeError("knowledge document not found")
    return document


async def _get_version(
    session: AsyncSession, tenant_id: str, version_id: str
) -> KnowledgeDocumentVersionModel:
    version = await session.scalar(
        select(KnowledgeDocumentVersionModel).where(
            KnowledgeDocumentVersionModel.tenant_id == tenant_id,
            KnowledgeDocumentVersionModel.version_id == version_id,
        )
    )
    if version is None:
        raise RuntimeError("knowledge version not found")
    return version


async def _get_job(
    session: AsyncSession, tenant_id: str, sync_job_id: str
) -> KnowledgeSyncJobModel:
    job = await session.scalar(
        select(KnowledgeSyncJobModel).where(
            KnowledgeSyncJobModel.tenant_id == tenant_id,
            KnowledgeSyncJobModel.sync_job_id == sync_job_id,
        )
    )
    if job is None:
        raise RuntimeError("knowledge sync job not found")
    return job


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _is_knowledge_version_identity_violation(exc: IntegrityError) -> bool:
    message = str(exc).casefold()
    sqlstate, constraint_name = _postgres_error_identity(exc)
    unique_violation = sqlstate == "23505" or "duplicate key value" in message
    identity = str(constraint_name).casefold() if constraint_name else message
    return unique_violation and any(
        marker in identity
        for marker in (
            "uq_knowledge_document_versions_tenant_id_document_id_co",
            "uq_kdv_tenant_snapshot_document_live",
        )
    )


def _postgres_error_identity(exc: IntegrityError) -> tuple[str | None, str | None]:
    pending: list[object] = [exc, exc.orig]
    visited: set[int] = set()
    sqlstate: str | None = None
    constraint_name: str | None = None
    while pending:
        current = pending.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))
        sqlstate = (
            sqlstate or getattr(current, "sqlstate", None) or getattr(current, "pgcode", None)
        )
        constraint_name = constraint_name or getattr(current, "constraint_name", None)
        diagnostics = getattr(current, "diag", None)
        if diagnostics is not None:
            constraint_name = constraint_name or getattr(diagnostics, "constraint_name", None)
        for attribute in ("__cause__", "__context__"):
            nested = getattr(current, attribute, None)
            if nested is not None:
                pending.append(nested)
    return sqlstate, constraint_name
