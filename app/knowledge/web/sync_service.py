import json
import logging
from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlparse
from uuid import NAMESPACE_URL, uuid5

from app.domain.models import ProductDataStatus, ProductSnapshot, ProductSnapshotActivation
from app.domain.ports import (
    EmbeddingProviderPort,
    KnowledgeChunk,
    KnowledgeChunkRecord,
    KnowledgeControlPlanePort,
    KnowledgeIndexerPort,
    KnowledgeVersionDraft,
    ProductCatalogPort,
    ProductIdentityConflictError,
    ProductLookup,
    ProductRecommendationIndexPort,
    SitePublicationCommitPort,
    SparseEmbeddingProviderPort,
)
from app.domain.rules.duplicate_product import (
    PRODUCT_IDENTITY_NORMALIZATION_VERSION,
    normalize_product_identity,
)
from app.domain.rules.knowledge_metadata import (
    KnowledgeChunkMetadataOverflowError,
    validate_document_metadata,
)
from app.knowledge.chunker import MarkdownChunker
from app.knowledge.models import ParsedKnowledgeDocument
from app.knowledge.web.crawler import WebsiteKnowledgeCrawler
from app.knowledge.web.errors import RetryableWebFetchError, StagingCleanupRequiredError
from app.knowledge.web.models import (
    StructuredWebDocument,
    WebCrawlPolicy,
    WebCrawlValidator,
    WebKnowledgeSyncReport,
)
from app.knowledge.web.product_snapshot import product_price_conflict, to_product_snapshot
from app.knowledge.web.quality import WebDocumentQualityGate

logger = logging.getLogger(__name__)


class WebKnowledgeSyncService:
    def __init__(
        self,
        *,
        crawler: WebsiteKnowledgeCrawler,
        chunker: MarkdownChunker,
        embedding_provider: EmbeddingProviderPort,
        sparse_embedding_provider: SparseEmbeddingProviderPort,
        indexer: KnowledgeIndexerPort,
        control_plane: KnowledgeControlPlanePort,
        product_catalog: ProductCatalogPort,
        publication_committer: SitePublicationCommitPort | None = None,
        product_recommendation_index: ProductRecommendationIndexPort | None = None,
        index_namespace: str = "hybrid-v1",
        quality_gate: WebDocumentQualityGate | None = None,
    ) -> None:
        self._crawler = crawler
        self._chunker = chunker
        self._embedding_provider = embedding_provider
        self._sparse_embedding_provider = sparse_embedding_provider
        self._indexer = indexer
        self._control_plane = control_plane
        self._product_catalog = product_catalog
        self._publication_committer = publication_committer
        self._product_recommendation_index = product_recommendation_index
        self._index_namespace = index_namespace
        self._quality_gate = quality_gate or WebDocumentQualityGate()
        self._staged_validators: dict[str, dict[str, WebCrawlValidator]] = {}

    async def validate(self, policy: WebCrawlPolicy) -> WebKnowledgeSyncReport:
        sync_job_id = await self._control_plane.begin_sync(
            tenant_id=policy.tenant_id,
            vault_path=policy.base_url,
        )
        active_documents = await self._control_plane.list_active_site_web_documents(
            tenant_id=policy.tenant_id,
            site_id=policy.site_id,
        )
        validators = {
            item.requested_url: item
            for item in (
                *(
                    WebCrawlValidator(
                        document_id=item.document_id,
                        version_id=item.version_id,
                        canonical_url=item.canonical_url,
                        requested_url=item.requested_url,
                        final_url=item.final_url,
                        etag=item.etag,
                        last_modified=item.last_modified,
                        product_key=item.product_key,
                    )
                    for item in active_documents
                ),
                *policy.validators,
            )
        }
        try:
            crawl = await self._crawler.crawl(
                replace(policy, validators=tuple(validators.values()))
            )
        except RetryableWebFetchError as error:
            await self._control_plane.complete_sync(
                tenant_id=policy.tenant_id,
                sync_job_id=sync_job_id,
                discovered_count=1,
                indexed_count=0,
                failed_count=1,
                error_summary={policy.seed_urls[0]: str(error)} if policy.seed_urls else {},
            )
            raise
        errors = dict(crawl.errors)
        failed_count = crawl.failed_count
        if crawl.truncated:
            failed_count += 1
            errors["crawl_limit"] = (
                f"crawl stopped at max_pages={policy.max_pages}; shadow scope is incomplete"
            )
        indexed_count = 0
        excluded_count = crawl.excluded_count
        changed_document_count = 0
        duplicate_product_count = 0
        seen_product_keys: set[str] = set()
        validated: list[WebCrawlValidator] = []

        for unchanged in crawl.unchanged_documents:
            previous = next(
                (
                    item
                    for item in validators.values()
                    if unchanged.canonical_url
                    in {item.canonical_url, item.requested_url, item.final_url}
                ),
                None,
            )
            if previous is not None:
                validated.append(
                    replace(
                        previous,
                        etag=unchanged.etag,
                        last_modified=unchanged.last_modified,
                    )
                )
            if unchanged.product_key:
                if unchanged.product_key in seen_product_keys:
                    duplicate_product_count += 1
                seen_product_keys.add(unchanged.product_key)

        for document in crawl.documents:
            quality = self._quality_gate.evaluate(document)
            if not quality.accepted:
                excluded_count += 1
                errors[document.canonical_url] = "excluded: quality_gate:" + ",".join(
                    quality.reason_codes
                )
                continue
            try:
                chunk_count, changed, validator, product_key = await self._validate_shadow_document(
                    document,
                    policy,
                    sync_job_id=sync_job_id,
                )
            except Exception as error:  # noqa: BLE001
                failed_count += 1
                errors[document.canonical_url] = _format_document_error(error)
                continue
            indexed_count += chunk_count
            changed_document_count += int(changed)
            if product_key:
                if product_key in seen_product_keys:
                    duplicate_product_count += 1
                seen_product_keys.add(product_key)
            validated.append(validator)

        await self._control_plane.complete_sync(
            tenant_id=policy.tenant_id,
            sync_job_id=sync_job_id,
            discovered_count=crawl.discovered_count,
            indexed_count=indexed_count,
            failed_count=failed_count,
            error_summary=errors,
        )
        return WebKnowledgeSyncReport(
            sync_job_id=sync_job_id,
            discovered_count=crawl.discovered_count,
            document_count=len(crawl.documents) + len(crawl.unchanged_documents),
            indexed_count=indexed_count,
            skipped_count=len(crawl.unchanged_documents),
            excluded_count=excluded_count,
            failed_count=failed_count,
            errors=errors,
            product_count=len(seen_product_keys),
            published=False,
            changed_document_count=changed_document_count,
            duplicate_count=_duplicate_count(errors),
            http_not_modified_count=len(crawl.unchanged_documents),
            duplicate_product_count=duplicate_product_count,
            validators=tuple(validated),
        )

    async def extract_identity(self, policy: WebCrawlPolicy) -> WebKnowledgeSyncReport:
        """Fetch one frozen URL and extract identity without indexing or staging it."""

        crawl = await self._crawler.crawl(policy)
        errors = dict(crawl.errors)
        if crawl.failed_count:
            return WebKnowledgeSyncReport(
                sync_job_id="identity-extraction",
                discovered_count=crawl.discovered_count,
                document_count=0,
                indexed_count=0,
                skipped_count=len(crawl.unchanged_documents),
                excluded_count=crawl.excluded_count,
                failed_count=crawl.failed_count,
                errors=errors,
                http_not_modified_count=len(crawl.unchanged_documents),
            )
        if crawl.unchanged_documents:
            unchanged = crawl.unchanged_documents[0]
            previous = _matching_validator(policy.validators, unchanged.canonical_url)
            validator = WebCrawlValidator(
                document_id=unchanged.document_id,
                version_id=unchanged.version_id,
                canonical_url=unchanged.canonical_url,
                requested_url=(
                    previous.requested_url if previous is not None else unchanged.canonical_url
                ),
                final_url=(previous.final_url if previous is not None else unchanged.canonical_url),
                etag=unchanged.etag,
                last_modified=unchanged.last_modified,
                product_key=unchanged.product_key,
            )
            return WebKnowledgeSyncReport(
                sync_job_id="identity-extraction",
                discovered_count=crawl.discovered_count,
                document_count=1,
                indexed_count=0,
                skipped_count=1,
                excluded_count=crawl.excluded_count,
                failed_count=0,
                errors=errors,
                http_not_modified_count=1,
                validators=(validator,),
            )
        if not crawl.documents:
            return WebKnowledgeSyncReport(
                sync_job_id="identity-extraction",
                discovered_count=crawl.discovered_count,
                document_count=0,
                indexed_count=0,
                skipped_count=0,
                excluded_count=max(1, crawl.excluded_count),
                failed_count=0,
                errors=errors,
            )
        document = crawl.documents[0]
        product = to_product_snapshot(document, snapshot_id="identity-extraction")
        validator = WebCrawlValidator(
            document_id=document.document_id,
            version_id="",
            canonical_url=document.canonical_url,
            requested_url=str(
                document.source_metadata.get("requested_url") or document.canonical_url
            ),
            final_url=str(document.source_metadata.get("final_url") or document.canonical_url),
            etag=_optional_text(document.source_metadata.get("etag")),
            last_modified=_optional_text(document.source_metadata.get("last_modified")),
            product_key=None if product is None else product.product_key,
            normalized_product_key=(None if product is None else product.normalized_product_key),
            normalization_version=(None if product is None else product.normalization_version),
        )
        return WebKnowledgeSyncReport(
            sync_job_id="identity-extraction",
            discovered_count=crawl.discovered_count,
            document_count=1,
            indexed_count=0,
            skipped_count=0,
            excluded_count=crawl.excluded_count,
            failed_count=0,
            errors=errors,
            validators=(validator,),
        )

    async def _validate_shadow_document(
        self,
        document: StructuredWebDocument,
        policy: WebCrawlPolicy,
        *,
        sync_job_id: str,
    ) -> tuple[int, bool, WebCrawlValidator, str | None]:
        semantic_hash = _semantic_content_hash(document)
        latest = await self._control_plane.get_latest_version(
            tenant_id=policy.tenant_id,
            document_id=document.document_id,
        )
        reusable = (
            latest is not None
            and latest.content_hash == semantic_hash
            and latest.index_namespace == self._index_namespace
            and latest.index_status in {"active", "indexed"}
        )
        chunk_count = 0
        if not reusable:
            parsed = _to_parsed_document(document, policy, semantic_hash)
            chunks = self._chunker.chunk(parsed)
            texts = [chunk.text for chunk in chunks]
            await self._embedding_provider.embed(texts)
            await self._sparse_embedding_provider.embed_sparse(texts)
            chunk_count = len(chunks)
        product = to_product_snapshot(document, snapshot_id=sync_job_id)
        product_key = None if product is None else product.product_key
        requested_url = str(document.source_metadata.get("requested_url") or document.canonical_url)
        return (
            chunk_count,
            not reusable,
            WebCrawlValidator(
                document_id=document.document_id,
                version_id=(
                    latest.version_id
                    if reusable and latest is not None
                    else str(uuid5(NAMESPACE_URL, f"{document.document_id}:{semantic_hash}"))
                ),
                canonical_url=document.canonical_url,
                requested_url=requested_url,
                final_url=str(document.source_metadata.get("final_url") or document.canonical_url),
                etag=_optional_text(document.source_metadata.get("etag")),
                last_modified=_optional_text(document.source_metadata.get("last_modified")),
                product_key=product_key,
                normalized_product_key=(
                    None if product is None else product.normalized_product_key
                ),
                normalization_version=(None if product is None else product.normalization_version),
            ),
            product_key,
        )

    async def begin_staged_sync(
        self,
        policy: WebCrawlPolicy,
        *,
        snapshot_id: str,
    ) -> None:
        sync_job_id = await self._control_plane.begin_sync(
            tenant_id=policy.tenant_id,
            vault_path=policy.base_url,
            sync_job_id=snapshot_id,
        )
        if sync_job_id != snapshot_id:
            raise RuntimeError("staged knowledge sync identity mismatch")
        active_documents = await self._control_plane.list_active_site_web_documents(
            tenant_id=policy.tenant_id,
            site_id=policy.site_id,
        )
        self._staged_validators[snapshot_id] = {
            item.requested_url: WebCrawlValidator(
                document_id=item.document_id,
                version_id=item.version_id,
                canonical_url=item.canonical_url,
                requested_url=item.requested_url,
                final_url=item.final_url,
                etag=item.etag,
                last_modified=item.last_modified,
                product_key=item.product_key,
            )
            for item in active_documents
        }
        await self._product_catalog.begin_snapshot(
            tenant_id=policy.tenant_id,
            site_id=policy.site_id,
            snapshot_id=snapshot_id,
            sync_job_id=snapshot_id,
            started_at=datetime.now(UTC),
        )

    async def stage_page(
        self,
        policy: WebCrawlPolicy,
        *,
        snapshot_id: str,
    ) -> WebKnowledgeSyncReport:
        staged_validators = self._staged_validators.get(snapshot_id, {})
        validators = {
            item.requested_url: item
            for item in (
                *staged_validators.values(),
                *policy.validators,
            )
        }
        crawl = await self._crawler.crawl(replace(policy, validators=tuple(validators.values())))
        errors = dict(crawl.errors)
        failed_count = crawl.failed_count
        if crawl.truncated:
            failed_count += 1
            errors["crawl_limit"] = "single-page production staging exceeded its frozen scope"
        if failed_count:
            return WebKnowledgeSyncReport(
                sync_job_id=snapshot_id,
                discovered_count=crawl.discovered_count,
                document_count=len(crawl.documents) + len(crawl.unchanged_documents),
                indexed_count=0,
                skipped_count=len(crawl.unchanged_documents),
                excluded_count=crawl.excluded_count,
                failed_count=failed_count,
                errors=errors,
                http_not_modified_count=len(crawl.unchanged_documents),
            )

        if crawl.unchanged_documents:
            unchanged = crawl.unchanged_documents[0]
            previous = _matching_validator(validators.values(), unchanged.canonical_url)
            validator = WebCrawlValidator(
                document_id=unchanged.document_id,
                version_id=unchanged.version_id,
                canonical_url=unchanged.canonical_url,
                requested_url=(
                    previous.requested_url if previous is not None else unchanged.canonical_url
                ),
                final_url=(previous.final_url if previous is not None else unchanged.canonical_url),
                etag=unchanged.etag,
                last_modified=unchanged.last_modified,
                product_key=unchanged.product_key,
                normalized_product_key=(
                    previous.normalized_product_key
                    if previous is not None
                    else normalize_product_identity(unchanged.product_key)
                ),
                normalization_version=(
                    previous.normalization_version
                    if previous is not None
                    else PRODUCT_IDENTITY_NORMALIZATION_VERSION
                    if normalize_product_identity(unchanged.product_key) is not None
                    else None
                ),
            )
            staged_product = await self._stage_existing_product(
                policy,
                snapshot_id=snapshot_id,
                validator=validator,
                fetched_at=unchanged.checked_at,
            )
            return WebKnowledgeSyncReport(
                sync_job_id=snapshot_id,
                discovered_count=crawl.discovered_count,
                document_count=1,
                indexed_count=0,
                skipped_count=1,
                excluded_count=crawl.excluded_count,
                failed_count=0,
                errors=errors,
                product_count=int(staged_product),
                http_not_modified_count=1,
                validators=(validator,),
            )

        if not crawl.documents:
            return WebKnowledgeSyncReport(
                sync_job_id=snapshot_id,
                discovered_count=crawl.discovered_count,
                document_count=0,
                indexed_count=0,
                skipped_count=0,
                excluded_count=max(1, crawl.excluded_count),
                failed_count=0,
                errors=errors,
            )

        document = crawl.documents[0]
        quality = self._quality_gate.evaluate(document)
        requested_url = str(document.source_metadata.get("requested_url") or document.canonical_url)
        prior = _matching_validator(validators.values(), requested_url, document.canonical_url)
        if not quality.accepted:
            reason_code = ",".join(quality.reason_codes)
            errors[document.canonical_url] = f"excluded: quality_gate:{reason_code}"
            await self._control_plane.record_ingestion_rejection(
                tenant_id=policy.tenant_id,
                sync_job_id=snapshot_id,
                source_path=document.canonical_url,
                reason_code=f"web_quality:{reason_code}",
            )
            validated = ()
            product_count = 0
            if prior is not None:
                validator = replace(
                    prior,
                    etag=_optional_text(document.source_metadata.get("etag")),
                    last_modified=_optional_text(document.source_metadata.get("last_modified")),
                )
                validated = (validator,)
                product_count = int(
                    await self._stage_existing_product(
                        policy,
                        snapshot_id=snapshot_id,
                        validator=validator,
                        fetched_at=document.fetched_at,
                    )
                )
            return WebKnowledgeSyncReport(
                sync_job_id=snapshot_id,
                discovered_count=crawl.discovered_count,
                document_count=1,
                indexed_count=0,
                skipped_count=0,
                excluded_count=crawl.excluded_count + 1,
                failed_count=0,
                errors=errors,
                product_count=product_count,
                validators=validated,
            )

        product = to_product_snapshot(document, snapshot_id=snapshot_id)
        product_staged = False
        try:
            # Run all in-memory metadata checks before creating any staging
            # artifact. This prevents a malformed page from leaving a product
            # row behind when indexing rejects it later.
            self._preflight_document(document, policy)
            indexed_count, skipped, version_id = await self._index_document(
                document,
                policy,
                snapshot_id=snapshot_id,
            )
            # Product staging is deliberately last.  If knowledge staging or
            # embedding fails, no product artifact exists to reconcile.  A
            # product conflict therefore only requires discarding the page's
            # newly-created knowledge artifacts, while preserving any prior
            # product already present in this snapshot.
            if product is not None:
                await self._stage_product_batch(policy, snapshot_id, [product])
                product_staged = True
        except Exception as original_error:
            cleanup_failed = False
            for cleanup_name, cleanup in (
                (
                    "qdrant",
                    self._indexer.discard_staged_document(
                        tenant_id=policy.tenant_id,
                        site_id=policy.site_id,
                        snapshot_id=snapshot_id,
                        document_id=document.document_id,
                    ),
                ),
                (
                    "postgres",
                    self._control_plane.discard_staged_document(
                        tenant_id=policy.tenant_id,
                        site_id=policy.site_id,
                        snapshot_id=snapshot_id,
                        document_id=document.document_id,
                    ),
                ),
            ):
                try:
                    await cleanup
                except Exception:  # noqa: BLE001
                    cleanup_failed = True
                    logger.exception(
                        "failed to discard knowledge artifacts after page failure",
                        extra={
                            "cleanup_store": cleanup_name,
                            "tenant_id": policy.tenant_id,
                            "site_id": policy.site_id,
                            "snapshot_id": snapshot_id,
                            "document_id": document.document_id,
                        },
                    )
            if product_staged and product is not None:
                try:
                    await self._product_catalog.discard_staged_product(
                        tenant_id=policy.tenant_id,
                        site_id=policy.site_id,
                        snapshot_id=snapshot_id,
                        normalized_product_key=(
                            product.normalized_product_key or product.product_key
                        ),
                        canonical_url=product.canonical_url,
                    )
                except Exception:  # noqa: BLE001
                    cleanup_failed = True
                    logger.exception(
                        "failed to discard product artifact after page failure",
                        extra={
                            "tenant_id": policy.tenant_id,
                            "site_id": policy.site_id,
                            "snapshot_id": snapshot_id,
                            "canonical_url": product.canonical_url,
                        },
                    )
            if cleanup_failed:
                raise StagingCleanupRequiredError(
                    "staging_cleanup_required: page artifacts could not be fully discarded"
                ) from original_error
            raise
        validator = WebCrawlValidator(
            document_id=document.document_id,
            version_id=version_id,
            canonical_url=document.canonical_url,
            requested_url=requested_url,
            final_url=str(document.source_metadata.get("final_url") or document.canonical_url),
            etag=_optional_text(document.source_metadata.get("etag")),
            last_modified=_optional_text(document.source_metadata.get("last_modified")),
            product_key=None if product is None else product.product_key,
            normalized_product_key=(None if product is None else product.normalized_product_key),
            normalization_version=(None if product is None else product.normalization_version),
        )
        return WebKnowledgeSyncReport(
            sync_job_id=snapshot_id,
            discovered_count=crawl.discovered_count,
            document_count=1,
            indexed_count=indexed_count,
            skipped_count=int(skipped),
            excluded_count=crawl.excluded_count,
            failed_count=0,
            errors=errors,
            product_count=int(product is not None),
            changed_document_count=int(indexed_count > 0),
            validators=(validator,),
        )

    async def publish_staged_sync(
        self,
        policy: WebCrawlPolicy,
        *,
        snapshot_id: str,
        active_version_ids: tuple[str, ...],
        staged_version_ids: tuple[str, ...],
        expected_chunk_count: int,
        expected_product_count: int,
        discovered_count: int,
        errors: dict[str, str],
    ) -> ProductSnapshotActivation:
        desired_versions = tuple(dict.fromkeys(active_version_ids))
        changed_versions = tuple(dict.fromkeys(staged_version_ids))
        preflight = getattr(self._control_plane, "validate_site_publication_versions", None)
        if preflight is not None:
            issues = await preflight(
                tenant_id=policy.tenant_id,
                site_id=policy.site_id,
                publication_id=snapshot_id,
                version_ids=desired_versions,
            )
            if issues:
                detail = "; ".join(issues[:20])
                raise RuntimeError(f"stale_version_reference: {detail}")
        postgres_chunks = await self._control_plane.count_indexed_chunks(
            tenant_id=policy.tenant_id,
            version_ids=changed_versions,
        )
        qdrant_points = await self._indexer.count_snapshot_points(
            tenant_id=policy.tenant_id,
            site_id=policy.site_id,
            snapshot_id=snapshot_id,
        )
        staged_products = await self._product_catalog.count_staged_products(
            tenant_id=policy.tenant_id,
            site_id=policy.site_id,
            snapshot_id=snapshot_id,
        )
        mismatches = {
            key: value
            for key, value in {
                "postgres_chunks": (
                    f"expected {expected_chunk_count}, found {postgres_chunks}"
                    if postgres_chunks != expected_chunk_count
                    else ""
                ),
                "qdrant_points": (
                    f"expected {expected_chunk_count}, found {qdrant_points}"
                    if qdrant_points != expected_chunk_count
                    else ""
                ),
                "staged_products": (
                    f"expected {expected_product_count}, found {staged_products}"
                    if staged_products != expected_product_count
                    else ""
                ),
            }.items()
            if value
        }
        if mismatches:
            detail = "; ".join(f"{key}: {value}" for key, value in mismatches.items())
            raise RuntimeError(f"staged publication reconciliation failed: {detail}")

        desired_chunk_count = await self._control_plane.count_indexed_chunks(
            tenant_id=policy.tenant_id,
            version_ids=desired_versions,
        )
        if desired_versions and desired_chunk_count == 0:
            raise RuntimeError("staged publication has no indexed target chunks")

        activation = await self._switch_site_publication(
            policy,
            snapshot_id=snapshot_id,
            desired_version_ids=desired_versions,
            desired_chunk_count=desired_chunk_count,
            discovered_count=discovered_count,
            indexed_count=expected_chunk_count,
            errors=errors,
        )
        self._staged_validators.pop(snapshot_id, None)
        return activation

    async def is_reusable_version(
        self,
        policy: WebCrawlPolicy,
        *,
        version_id: str,
        publication_id: str,
    ) -> bool:
        checker = getattr(self._control_plane, "is_reusable_site_version", None)
        if checker is None:
            return True
        return await checker(
            tenant_id=policy.tenant_id,
            site_id=policy.site_id,
            version_id=version_id,
            publication_id=publication_id,
        )

    async def _switch_site_publication(
        self,
        policy: WebCrawlPolicy,
        *,
        snapshot_id: str,
        desired_version_ids: tuple[str, ...],
        desired_chunk_count: int,
        discovered_count: int,
        indexed_count: int,
        errors: dict[str, str],
    ) -> ProductSnapshotActivation:
        switch_origin = await self._control_plane.begin_site_publication_switch(
            tenant_id=policy.tenant_id,
            site_id=policy.site_id,
            publication_id=snapshot_id,
        )
        previous_publication_id = switch_origin.active_publication_id
        previous_version_ids: tuple[str, ...] = ()
        previous_product_snapshot_id: str | None = None
        previous_chunk_count = 0
        rollback_context_ready = False
        try:
            previous_version_ids = await self._previous_publication_versions(
                policy,
                publication_id=previous_publication_id,
                allow_current_fallback=switch_origin.state == "active",
            )
            previous_product = await self._product_catalog.get_active_summary(
                tenant_id=policy.tenant_id,
                site_id=policy.site_id,
            )
            previous_product_snapshot_id = previous_publication_id or (
                previous_product.snapshot_id if switch_origin.state == "active" else None
            )
            previous_chunk_count = await self._control_plane.count_indexed_chunks(
                tenant_id=policy.tenant_id,
                version_ids=previous_version_ids,
            )
            rollback_context_ready = True
            await self._indexer.activate_site_versions(
                tenant_id=policy.tenant_id,
                site_id=policy.site_id,
                version_ids=desired_version_ids,
                expected_point_count=desired_chunk_count,
            )
            completed_at = datetime.now(UTC)
            if self._publication_committer is not None:
                activation = await self._publication_committer.commit_site_publication(
                    tenant_id=policy.tenant_id,
                    site_id=policy.site_id,
                    publication_id=snapshot_id,
                    version_ids=desired_version_ids,
                    discovered_count=discovered_count,
                    indexed_count=indexed_count,
                    error_summary=errors,
                    completed_at=completed_at,
                )
            else:
                await self._control_plane.activate_site_versions(
                    tenant_id=policy.tenant_id,
                    site_id=policy.site_id,
                    version_ids=desired_version_ids,
                )
                activation = await self._product_catalog.activate_snapshot(
                    tenant_id=policy.tenant_id,
                    site_id=policy.site_id,
                    snapshot_id=snapshot_id,
                    completed_at=completed_at,
                )
                await self._control_plane.complete_sync(
                    tenant_id=policy.tenant_id,
                    sync_job_id=snapshot_id,
                    discovered_count=discovered_count,
                    indexed_count=indexed_count,
                    failed_count=0,
                    error_summary=errors,
                )
                await self._control_plane.complete_site_publication_switch(
                    tenant_id=policy.tenant_id,
                    site_id=policy.site_id,
                    publication_id=snapshot_id,
                )
        except Exception as error:
            if rollback_context_ready:
                await self._rollback_site_publication(
                    policy,
                    snapshot_id=snapshot_id,
                    previous_publication_id=previous_publication_id,
                    previous_version_ids=previous_version_ids,
                    previous_chunk_count=previous_chunk_count,
                    previous_product_snapshot_id=previous_product_snapshot_id,
                    previous_publication_state=switch_origin.state,
                    error=error,
                )
            elif switch_origin.state == "active":
                await self._control_plane.restore_site_publication(
                    tenant_id=policy.tenant_id,
                    site_id=policy.site_id,
                    failed_publication_id=snapshot_id,
                    previous_publication_id=previous_publication_id,
                    error_code=type(error).__name__,
                )
            else:
                await self._control_plane.require_site_publication_recovery(
                    tenant_id=policy.tenant_id,
                    site_id=policy.site_id,
                    publication_id=snapshot_id,
                    error_code=f"switch_context_failed:{type(error).__name__}",
                )
            try:
                await self._control_plane.fail_sync(
                    tenant_id=policy.tenant_id,
                    sync_job_id=snapshot_id,
                    error_code=type(error).__name__,
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "failed to record knowledge publication failure",
                    extra={
                        "tenant_id": policy.tenant_id,
                        "site_id": policy.site_id,
                        "snapshot_id": snapshot_id,
                    },
                )
            raise

        await self._refresh_product_recommendation_index(policy, snapshot_id)
        return activation

    async def _previous_publication_versions(
        self,
        policy: WebCrawlPolicy,
        *,
        publication_id: str | None,
        allow_current_fallback: bool,
    ) -> tuple[str, ...]:
        if publication_id is None:
            if not allow_current_fallback:
                return ()
            return await self._control_plane.list_active_site_version_ids(
                tenant_id=policy.tenant_id,
                site_id=policy.site_id,
            )
        versions = await self._control_plane.list_publication_version_ids(
            tenant_id=policy.tenant_id,
            site_id=policy.site_id,
            publication_id=publication_id,
        )
        if versions:
            return versions
        if allow_current_fallback:
            return await self._control_plane.list_active_site_version_ids(
                tenant_id=policy.tenant_id,
                site_id=policy.site_id,
            )
        raise RuntimeError("previous publication manifest is unavailable for recovery")

    async def _rollback_site_publication(
        self,
        policy: WebCrawlPolicy,
        *,
        snapshot_id: str,
        previous_publication_id: str | None,
        previous_version_ids: tuple[str, ...],
        previous_chunk_count: int,
        previous_product_snapshot_id: str | None,
        previous_publication_state: str,
        error: Exception,
    ) -> None:
        rollback_errors: list[Exception] = []
        try:
            await self._indexer.activate_site_versions(
                tenant_id=policy.tenant_id,
                site_id=policy.site_id,
                version_ids=previous_version_ids,
                expected_point_count=(previous_chunk_count if previous_version_ids else None),
            )
        except Exception as rollback_error:  # noqa: BLE001
            rollback_errors.append(rollback_error)
        try:
            await self._control_plane.activate_site_versions(
                tenant_id=policy.tenant_id,
                site_id=policy.site_id,
                version_ids=previous_version_ids,
            )
        except Exception as rollback_error:  # noqa: BLE001
            rollback_errors.append(rollback_error)
        try:
            await self._product_catalog.restore_snapshot(
                tenant_id=policy.tenant_id,
                site_id=policy.site_id,
                failed_snapshot_id=snapshot_id,
                previous_snapshot_id=previous_product_snapshot_id,
                restored_at=datetime.now(UTC),
                error_summary={"finalization": f"{type(error).__name__}: {error}"},
            )
        except Exception as rollback_error:  # noqa: BLE001
            rollback_errors.append(rollback_error)

        if not rollback_errors:
            try:
                if previous_publication_state == "active":
                    await self._control_plane.restore_site_publication(
                        tenant_id=policy.tenant_id,
                        site_id=policy.site_id,
                        failed_publication_id=snapshot_id,
                        previous_publication_id=previous_publication_id,
                        error_code=type(error).__name__,
                    )
                else:
                    await self._control_plane.require_site_publication_recovery(
                        tenant_id=policy.tenant_id,
                        site_id=policy.site_id,
                        publication_id=snapshot_id,
                        error_code=f"recovery_publication_failed:{type(error).__name__}",
                    )
                return
            except Exception as rollback_error:  # noqa: BLE001
                rollback_errors.append(rollback_error)

        recovery_code = f"rollback_failed:{type(rollback_errors[0]).__name__}"
        try:
            await self._control_plane.require_site_publication_recovery(
                tenant_id=policy.tenant_id,
                site_id=policy.site_id,
                publication_id=snapshot_id,
                error_code=recovery_code,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "failed to persist required publication recovery state",
                extra={
                    "tenant_id": policy.tenant_id,
                    "site_id": policy.site_id,
                    "snapshot_id": snapshot_id,
                },
            )
        logger.error(
            "site publication rollback was incomplete",
            extra={
                "tenant_id": policy.tenant_id,
                "site_id": policy.site_id,
                "snapshot_id": snapshot_id,
                "rollback_errors": [type(item).__name__ for item in rollback_errors],
            },
        )

    async def abort_staged_sync(
        self,
        policy: WebCrawlPolicy,
        *,
        snapshot_id: str,
        errors: dict[str, str],
        error_code: str,
    ) -> None:
        try:
            await self._fail_publication(policy, snapshot_id, errors)
            await self._control_plane.fail_sync(
                tenant_id=policy.tenant_id,
                sync_job_id=snapshot_id,
                error_code=error_code,
            )
        finally:
            self._staged_validators.pop(snapshot_id, None)

    async def _stage_existing_product(
        self,
        policy: WebCrawlPolicy,
        *,
        snapshot_id: str,
        validator: WebCrawlValidator,
        fetched_at: datetime,
    ) -> bool:
        if not validator.product_key:
            return False
        product = await self._product_catalog.find_exact(
            ProductLookup(
                tenant_id=policy.tenant_id,
                site_id=policy.site_id,
                sku=validator.product_key,
                mpn=validator.product_key,
                canonical_url=validator.canonical_url,
            )
        )
        if product is None:
            return False
        await self._stage_product_batch(
            policy,
            snapshot_id,
            [
                replace(
                    product,
                    snapshot_id=snapshot_id,
                    fetched_at=fetched_at,
                    etag=validator.etag,
                    last_modified=validator.last_modified,
                    status=ProductDataStatus.VALID,
                    missing_count=0,
                )
            ],
        )
        return True

    async def sync(self, policy: WebCrawlPolicy) -> WebKnowledgeSyncReport:
        sync_job_id = await self._control_plane.begin_sync(
            tenant_id=policy.tenant_id,
            vault_path=policy.base_url,
        )
        indexed_count = 0
        skipped_count = 0
        failed_count = 0
        quality_excluded_count = 0
        changed_document_count = 0
        duplicate_product_count = 0
        seen_product_keys: set[str] = set()
        errors: dict[str, str] = {}
        active_version_ids: list[str] = []
        publication_activated = False
        active_web_documents = await self._control_plane.list_active_site_web_documents(
            tenant_id=policy.tenant_id,
            site_id=policy.site_id,
        )
        await self._product_catalog.begin_snapshot(
            tenant_id=policy.tenant_id,
            site_id=policy.site_id,
            snapshot_id=sync_job_id,
            sync_job_id=sync_job_id,
            started_at=datetime.now(UTC),
        )
        try:
            active_validators = {
                item.requested_url: WebCrawlValidator(
                    document_id=item.document_id,
                    version_id=item.version_id,
                    canonical_url=item.canonical_url,
                    requested_url=item.requested_url,
                    final_url=item.final_url,
                    etag=item.etag,
                    last_modified=item.last_modified,
                    product_key=item.product_key,
                )
                for item in active_web_documents
            }
            active_validators.update({item.requested_url: item for item in policy.validators})
            crawl = await self._crawler.crawl(
                replace(policy, validators=tuple(active_validators.values()))
            )
            errors.update(crawl.errors)
            failed_count += crawl.failed_count
            if crawl.truncated:
                failed_count += 1
                errors["crawl_limit"] = (
                    f"crawl stopped at max_pages={policy.max_pages}; incomplete snapshot refused"
                )
            document_count = len(crawl.documents) + len(crawl.unchanged_documents)
            if failed_count:
                await self._fail_publication(policy, sync_job_id, errors)
                await self._control_plane.complete_sync(
                    tenant_id=policy.tenant_id,
                    sync_job_id=sync_job_id,
                    discovered_count=crawl.discovered_count,
                    indexed_count=0,
                    failed_count=failed_count,
                    error_summary=errors,
                )
                return WebKnowledgeSyncReport(
                    sync_job_id=sync_job_id,
                    discovered_count=crawl.discovered_count,
                    document_count=document_count,
                    indexed_count=0,
                    skipped_count=0,
                    excluded_count=crawl.excluded_count,
                    failed_count=failed_count,
                    errors=errors,
                    published=False,
                    duplicate_count=_duplicate_count(errors),
                    http_not_modified_count=len(crawl.unchanged_documents),
                    duplicate_product_count=duplicate_product_count,
                )
            products: list[ProductSnapshot] = []
            if crawl.unchanged_documents:
                active_products = await self._product_catalog.list_active_products(
                    tenant_id=policy.tenant_id,
                    site_id=policy.site_id,
                    limit=policy.max_pages,
                )
                products_by_url = {item.canonical_url: item for item in active_products}
                for unchanged in crawl.unchanged_documents:
                    if unchanged.product_key:
                        if unchanged.product_key in seen_product_keys:
                            duplicate_product_count += 1
                        else:
                            seen_product_keys.add(unchanged.product_key)
                    active_version_ids.append(unchanged.version_id)
                    skipped_count += 1
                    previous_product = products_by_url.get(unchanged.canonical_url)
                    if previous_product is None:
                        continue
                    products.append(
                        replace(
                            previous_product,
                            snapshot_id=sync_job_id,
                            fetched_at=unchanged.checked_at,
                            etag=unchanged.etag,
                            last_modified=unchanged.last_modified,
                            status=ProductDataStatus.VALID,
                            missing_count=0,
                        )
                    )
                    if len(products) >= policy.batch_size:
                        await self._stage_product_batch(policy, sync_job_id, products)
                        products.clear()
            for document in crawl.documents:
                quality = self._quality_gate.evaluate(document)
                if not quality.accepted:
                    quality_excluded_count += 1
                    reason_code = ",".join(quality.reason_codes)
                    errors[document.canonical_url] = f"excluded: quality_gate:{reason_code}"
                    await self._control_plane.record_ingestion_rejection(
                        tenant_id=policy.tenant_id,
                        sync_job_id=sync_job_id,
                        source_path=document.canonical_url,
                        reason_code=f"web_quality:{reason_code}",
                    )
                    latest = await self._control_plane.get_latest_version(
                        tenant_id=policy.tenant_id,
                        document_id=document.document_id,
                    )
                    if latest is not None and latest.index_status in {"active", "indexed"}:
                        active_version_ids.append(latest.version_id)
                    continue
                try:
                    self._preflight_document(document, policy)
                    price_conflict = product_price_conflict(document)
                    if price_conflict:
                        raise ValueError("product_price_conflict:" + ",".join(price_conflict))
                    product = to_product_snapshot(document, snapshot_id=sync_job_id)
                    if product is not None:
                        if product.product_key in seen_product_keys:
                            duplicate_product_count += 1
                        else:
                            seen_product_keys.add(product.product_key)
                        products.append(product)
                        if len(products) >= policy.batch_size:
                            await self._stage_product_batch(policy, sync_job_id, products)
                            products.clear()
                    indexed, skipped, version_id = await self._index_document(
                        document,
                        policy,
                        snapshot_id=sync_job_id,
                    )
                    indexed_count += indexed
                    skipped_count += int(skipped)
                    active_version_ids.append(version_id)
                    changed_document_count += int(indexed > 0)
                except Exception as error:  # noqa: BLE001
                    failed_count += 1
                    errors[document.canonical_url] = _format_document_error(error)
            if products:
                await self._stage_product_batch(policy, sync_job_id, products)
            if failed_count:
                await self._fail_publication(policy, sync_job_id, errors)
                await self._control_plane.complete_sync(
                    tenant_id=policy.tenant_id,
                    sync_job_id=sync_job_id,
                    discovered_count=crawl.discovered_count,
                    indexed_count=0,
                    failed_count=failed_count,
                    error_summary=errors,
                )
                return WebKnowledgeSyncReport(
                    sync_job_id=sync_job_id,
                    discovered_count=crawl.discovered_count,
                    document_count=document_count,
                    indexed_count=0,
                    skipped_count=skipped_count,
                    excluded_count=crawl.excluded_count + quality_excluded_count,
                    failed_count=failed_count,
                    errors=errors,
                    published=False,
                    changed_document_count=changed_document_count,
                    duplicate_count=_duplicate_count(errors),
                    http_not_modified_count=len(crawl.unchanged_documents),
                    duplicate_product_count=duplicate_product_count,
                )
            active_version_ids = list(dict.fromkeys(active_version_ids))
            active_chunk_count = await self._control_plane.count_indexed_chunks(
                tenant_id=policy.tenant_id,
                version_ids=active_version_ids,
            )
            activation = await self._switch_site_publication(
                policy,
                snapshot_id=sync_job_id,
                desired_version_ids=tuple(active_version_ids),
                desired_chunk_count=active_chunk_count,
                discovered_count=crawl.discovered_count,
                indexed_count=indexed_count,
                errors=errors,
            )
            publication_activated = True
            return WebKnowledgeSyncReport(
                sync_job_id=sync_job_id,
                discovered_count=crawl.discovered_count,
                document_count=document_count,
                indexed_count=indexed_count,
                skipped_count=skipped_count,
                excluded_count=crawl.excluded_count + quality_excluded_count,
                failed_count=failed_count,
                errors=errors,
                product_count=activation.activated_count,
                pending_removal_count=activation.pending_removal_count,
                expired_count=activation.expired_count,
                published=True,
                changed_document_count=changed_document_count,
                duplicate_count=_duplicate_count(errors),
                http_not_modified_count=len(crawl.unchanged_documents),
                duplicate_product_count=duplicate_product_count,
            )
        except Exception as error:
            if not publication_activated:
                await self._fail_publication(
                    policy,
                    sync_job_id,
                    {**errors, "fatal": f"{type(error).__name__}: {error}"},
                )
            await self._control_plane.fail_sync(
                tenant_id=policy.tenant_id,
                sync_job_id=sync_job_id,
                error_code=type(error).__name__,
            )
            raise

    async def _index_document(
        self,
        document: StructuredWebDocument,
        policy: WebCrawlPolicy,
        *,
        snapshot_id: str,
    ) -> tuple[int, bool, str]:
        latest = await self._control_plane.get_latest_version(
            tenant_id=policy.tenant_id,
            document_id=document.document_id,
        )
        semantic_content_hash = _semantic_content_hash(document)
        same_content = latest is not None and latest.content_hash == semantic_content_hash
        if (
            same_content
            and latest.index_namespace == self._index_namespace
            and latest.index_status in {"active", "indexed"}
        ):
            return 0, True, latest.version_id
        version_id = (
            latest.version_id
            if same_content and latest is not None
            else str(uuid5(NAMESPACE_URL, f"{document.document_id}:{semantic_content_hash}"))
        )
        parsed = _to_parsed_document(document, policy, semantic_content_hash)
        text_chunks = self._chunker.chunk(parsed)
        point_ids = [chunk.chunk_id for chunk in text_chunks]
        staged_version_id = await self._control_plane.stage_version(
            draft=_to_version_draft(
                document,
                policy,
                version_id,
                self._index_namespace,
                semantic_content_hash,
                snapshot_id,
            ),
            chunks=[
                KnowledgeChunkRecord(
                    chunk_id=chunk.chunk_id,
                    content_hash=chunk.content_hash,
                    sequence=chunk.sequence,
                    heading=chunk.heading,
                    chunk_type=chunk.chunk_type,
                    parent_chunk_id=chunk.parent_chunk_id,
                    section_path=chunk.section_path,
                    fact_keys=chunk.fact_keys,
                )
                for chunk in text_chunks
            ],
        )
        texts = [chunk.text for chunk in text_chunks]
        vectors = await self._embedding_provider.embed(texts)
        sparse_vectors = await self._sparse_embedding_provider.embed_sparse(texts)
        projection = [
            KnowledgeChunk(
                tenant_id=policy.tenant_id,
                chunk_id=point_id,
                document_id=document.document_id,
                version_id=staged_version_id,
                text=chunk.text,
                vector=vector,
                sparse_vector=sparse_vector,
                metadata={
                    **parsed.metadata,
                    "partition_id": policy.tenant_id,
                    "knowledge_scope": "site",
                    "source_type": "website_html",
                    "is_active": False,
                    "snapshot_id": snapshot_id,
                    "version_id": staged_version_id,
                    "source": document.canonical_url,
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
                point_ids,
                text_chunks,
                vectors,
                sparse_vectors,
                strict=True,
            )
        ]
        await self._indexer.upsert(projection)
        await self._control_plane.mark_indexed(
            tenant_id=policy.tenant_id,
            document_id=document.document_id,
            version_id=staged_version_id,
            point_ids=point_ids,
            index_namespace=self._index_namespace,
            activate=False,
        )
        return len(projection), False, staged_version_id

    async def _stage_product_batch(
        self,
        policy: WebCrawlPolicy,
        snapshot_id: str,
        products: list[ProductSnapshot],
    ) -> None:
        try:
            await self._product_catalog.stage_products(
                tenant_id=policy.tenant_id,
                site_id=policy.site_id,
                snapshot_id=snapshot_id,
                products=tuple(products),
            )
        except ProductIdentityConflictError as error:
            await self._control_plane.record_conflict(
                tenant_id=policy.tenant_id,
                question=(
                    f"product_identity:{policy.site_id}:{error.product_key}:"
                    f"{','.join(error.fields)}:{error.existing_url}:{error.candidate_url}"
                ),
                version_ids=(),
                risk_level=2,
            )
            raise

    def _preflight_document(
        self,
        document: StructuredWebDocument,
        policy: WebCrawlPolicy,
    ) -> None:
        """Validate page metadata and chunks without touching external stores."""
        semantic_hash = _semantic_content_hash(document)
        parsed = _to_parsed_document(document, policy, semantic_hash)
        validate_document_metadata(
            title=document.title,
            document_id=document.document_id,
            url=document.canonical_url,
        )
        self._chunker.chunk(parsed)

    async def _refresh_product_recommendation_index(
        self,
        policy: WebCrawlPolicy,
        snapshot_id: str,
    ) -> None:
        if self._product_recommendation_index is None:
            return
        try:
            products = await self._product_catalog.list_active_products(
                tenant_id=policy.tenant_id,
                site_id=policy.site_id,
                limit=10_000,
            )
            await self._product_recommendation_index.replace_site_snapshot(
                tenant_id=policy.tenant_id,
                site_id=policy.site_id,
                snapshot_id=snapshot_id,
                products=products,
            )
        except Exception:
            logger.exception(
                "product recommendation index refresh failed",
                extra={
                    "tenant_id": policy.tenant_id,
                    "site_id": policy.site_id,
                    "snapshot_id": snapshot_id,
                },
            )

    async def _fail_publication(
        self,
        policy: WebCrawlPolicy,
        snapshot_id: str,
        errors: dict[str, str],
    ) -> None:
        await self._indexer.discard_snapshot(
            tenant_id=policy.tenant_id,
            site_id=policy.site_id,
            snapshot_id=snapshot_id,
        )
        await self._control_plane.discard_site_snapshot_versions(
            tenant_id=policy.tenant_id,
            site_id=policy.site_id,
            snapshot_id=snapshot_id,
        )
        await self._product_catalog.fail_snapshot(
            tenant_id=policy.tenant_id,
            site_id=policy.site_id,
            snapshot_id=snapshot_id,
            error_summary=errors,
        )


def _to_parsed_document(
    document: StructuredWebDocument,
    policy: WebCrawlPolicy,
    semantic_content_hash: str,
) -> ParsedKnowledgeDocument:
    return ParsedKnowledgeDocument(
        path=Path(f"{document.document_id}.md"),
        metadata=_metadata(document, policy),
        body=_semantic_body(document),
        internal_links=document.internal_links,
        content_hash=semantic_content_hash,
        # Web documents must never fall back to Markdown prefix heuristics;
        # only parser-approved blocks are allowed to populate heading metadata.
        heading_blocks=document.heading_blocks or frozenset(),
    )


def _to_version_draft(
    document: StructuredWebDocument,
    policy: WebCrawlPolicy,
    version_id: str,
    index_namespace: str,
    semantic_content_hash: str,
    snapshot_id: str,
) -> KnowledgeVersionDraft:
    metadata = _metadata(document, policy)
    metadata["snapshot_id"] = snapshot_id
    return KnowledgeVersionDraft(
        version_id=version_id,
        tenant_id=policy.tenant_id,
        document_id=document.document_id,
        source_path=document.canonical_url,
        title=document.title,
        version=semantic_content_hash[:12],
        content_hash=semantic_content_hash,
        status="published",
        authority_level=policy.authority_level,
        priority=policy.priority,
        language=document.language,
        audience="public",
        effective_from=None,
        effective_to=None,
        metadata={**metadata, "index_namespace": index_namespace},
        internal_links=document.internal_links,
    )


def _metadata(document: StructuredWebDocument, policy: WebCrawlPolicy) -> dict[str, object]:
    requested_url = str(document.source_metadata.get("requested_url") or document.canonical_url)
    final_url = str(document.source_metadata.get("final_url") or document.canonical_url)
    source_metadata = {
        key: value
        for key, value in document.source_metadata.items()
        if key not in {"content_topics", "freshness_class", "max_age_seconds"}
    }
    return {
        "document_id": document.document_id,
        "tenant_id": policy.tenant_id,
        "site_id": policy.site_id,
        "title": document.title,
        "category": document.category,
        "audience": "public",
        "product": _product_identity(document.product),
        "region": "global",
        "language": document.language,
        "status": "published",
        "authority_level": policy.authority_level,
        "priority": policy.priority,
        "version": document.content_hash[:12],
        "owner_role": "website_owner",
        "reviewer": "automated_web_ingestion",
        "updated_at": document.fetched_at.isoformat(),
        "canonical_url": document.canonical_url,
        "requested_url": requested_url,
        "final_url": final_url,
        "canonical_path": urlparse(document.canonical_url).path or "/",
        "requested_path": urlparse(requested_url).path or "/",
        "final_path": urlparse(final_url).path or "/",
        "source_type": "website_html",
        **source_metadata,
        "content_topics": list(
            document.source_metadata.get("content_topics")
            or (["product_description"] if document.product else ["website_content"])
        ),
        "freshness_class": "catalog" if document.product else "published_content",
    }


def _semantic_body(document: StructuredWebDocument) -> str:
    if not document.product:
        return document.body
    blocks = document.body.split("\n\n")
    result: list[str] = []
    skip_structured_payload = False
    for block in blocks:
        stripped = block.strip()
        if stripped.casefold() == "## structured product data":
            skip_structured_payload = True
            continue
        if skip_structured_payload:
            skip_structured_payload = False
            continue
        result.append(block)
    return "\n\n".join(result).strip() or f"# {document.title}"


def _semantic_content_hash(document: StructuredWebDocument) -> str:
    payload = {
        "body": _semantic_body(document),
        "canonical_url": document.canonical_url,
        "product_identity": _product_identity(document.product),
    }
    return sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _product_identity(product: dict[str, object] | None) -> dict[str, object]:
    if not product:
        return {}
    return {
        key: product[key]
        for key in ("sku", "mpn", "name", "brand", "category")
        if product.get(key) not in (None, "")
    }


def _format_document_error(error: Exception) -> str:
    if isinstance(error, KnowledgeChunkMetadataOverflowError):
        code = (
            "knowledge_chunk_heading_too_long"
            if error.field == "heading"
            else "knowledge_chunk_metadata_too_long"
        )
        return f"{code}: {error}"
    return f"{type(error).__name__}: {error}"


def _duplicate_count(errors: dict[str, str]) -> int:
    return sum("duplicate" in value.casefold() for value in errors.values())


def _matching_validator(
    validators: Iterable[WebCrawlValidator],
    *urls: str,
) -> WebCrawlValidator | None:
    expected = set(urls)
    return next(
        (
            validator
            for validator in validators
            if expected & {validator.requested_url, validator.canonical_url, validator.final_url}
        ),
        None,
    )


def _optional_text(value: object) -> str | None:
    return str(value) if value not in (None, "") else None
