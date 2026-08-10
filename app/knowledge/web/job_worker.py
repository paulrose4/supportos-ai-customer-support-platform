import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from time import monotonic, perf_counter
from uuid import NAMESPACE_URL, uuid5

from app.domain.models.web_crawl_manifest import WebCrawlPageState
from app.domain.models.web_sync_job import (
    WebSyncJob,
    WebSyncJobItem,
    WebSyncJobItemStatus,
    WebSyncJobReport,
    WebSyncJobStatus,
    WebSyncMode,
)
from app.domain.ports import KnowledgeVersionSchemaInvariantError
from app.domain.ports.web_crawl_manifests import WebCrawlManifestStorePort
from app.domain.ports.web_sync_jobs import WebSyncJobStorePort
from app.knowledge.web.errors import RetryableWebFetchError
from app.knowledge.web.execution_budget import WebSyncExecutionBudget, WebSyncPageLease
from app.knowledge.web.models import WebCrawlPolicy, WebCrawlValidator, WebKnowledgeSyncReport
from app.knowledge.web.sync_service import WebKnowledgeSyncService


class _LeaseMonitorStopped(RuntimeError):
    """The durable lease is intentionally left to recovery after a stalled turn."""


class WebSyncJobWorker:
    def __init__(
        self,
        *,
        store: WebSyncJobStorePort,
        manifest_store: WebCrawlManifestStorePort,
        synchronizer: WebKnowledgeSyncService,
        concurrency: int = 4,
        lease_seconds: int = 120,
        heartbeat_seconds: int = 30,
        staging_retention_hours: int = 48,
        prepare_batch_size: int = 500,
        execution_budget: WebSyncExecutionBudget | None = None,
        max_no_progress_seconds: float | None = None,
        finalization_timeout_seconds: float = 960.0,
    ) -> None:
        self._store = store
        self._manifest_store = manifest_store
        self._synchronizer = synchronizer
        if not 1 <= concurrency <= 16:
            raise ValueError("website sync worker concurrency must be between 1 and 16")
        self._concurrency = concurrency
        self._lease_seconds = lease_seconds
        self._heartbeat_seconds = heartbeat_seconds
        self._staging_retention = timedelta(hours=staging_retention_hours)
        if not 1 <= prepare_batch_size <= 5000:
            raise ValueError("website sync preparation batch size must be between 1 and 5000")
        self._prepare_batch_size = prepare_batch_size
        self._execution_budget = execution_budget or WebSyncExecutionBudget(
            page_slots=concurrency,
            finalization_slots=1,
        )
        if max_no_progress_seconds is None:
            max_no_progress_seconds = max(heartbeat_seconds * 2, lease_seconds * 0.75)
        if max_no_progress_seconds <= heartbeat_seconds:
            raise ValueError("website sync no-progress limit must exceed the heartbeat interval")
        if finalization_timeout_seconds <= 0:
            raise ValueError("website sync finalization timeout must be positive")
        self._max_no_progress_seconds = max_no_progress_seconds
        self._finalization_timeout_seconds = finalization_timeout_seconds

    async def run_once(
        self,
        *,
        tenant_id: str,
        worker_id: str,
        max_pages: int | None = None,
        max_seconds: float | None = None,
    ) -> bool:
        if max_pages is not None and max_pages < 1:
            raise ValueError("website sync work quantum pages must be positive")
        if max_seconds is not None and max_seconds <= 0:
            raise ValueError("website sync work quantum seconds must be positive")
        deadline = perf_counter() + max_seconds if max_seconds is not None else None
        claimed_at = datetime.now(UTC)
        job = await self._store.claim_next(
            tenant_id=tenant_id,
            worker_id=worker_id,
            claimed_at=claimed_at,
            lease_expires_at=claimed_at + timedelta(seconds=self._lease_seconds),
        )
        if job is None:
            return False

        progress = _ProgressTracker(self._max_no_progress_seconds)
        heartbeat = asyncio.create_task(
            self._heartbeat(
                tenant_id=tenant_id,
                job_id=job.job_id,
                worker_id=worker_id,
                progress=progress,
            )
        )
        staged_started = False
        publication_activated = False
        try:
            if job.status is WebSyncJobStatus.CLEANUP_PENDING:
                return await self._cleanup_expired_job(
                    job,
                    worker_id=worker_id,
                    heartbeat=heartbeat,
                    progress=progress,
                )
            if job.status is WebSyncJobStatus.PREPARING:
                prepared = await self._prepare_job(
                    job,
                    worker_id=worker_id,
                    max_pages=max_pages,
                    deadline=deadline,
                    heartbeat=heartbeat,
                    progress=progress,
                )
                if not prepared:
                    return True
                if max_pages is not None or max_seconds is not None:
                    return True
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
                return await self.run_once(tenant_id=tenant_id, worker_id=worker_id)
            job_policy = _job_policy(job)
            if job.mode is WebSyncMode.PRODUCTION:
                await self._synchronizer.begin_staged_sync(
                    job_policy,
                    snapshot_id=job.job_id,
                )
                staged_started = True
            processed_pages = 0
            while True:
                current = await self._store.get(tenant_id=tenant_id, job_id=job.job_id)
                if current is None:
                    raise RuntimeError("website sync job disappeared while it was running")
                if current.cancel_requested_at is not None:
                    if job.mode is WebSyncMode.PRODUCTION:
                        await self._synchronizer.abort_staged_sync(
                            job_policy,
                            snapshot_id=job.job_id,
                            errors={"canceled": "production synchronization was canceled"},
                            error_code="canceled",
                        )
                    await self._store.cancel(
                        tenant_id=tenant_id,
                        job_id=job.job_id,
                        worker_id=worker_id,
                        canceled_at=datetime.now(UTC),
                    )
                    return True
                remaining_pages = None if max_pages is None else max(0, max_pages - processed_pages)
                if remaining_pages == 0 or _deadline_reached(deadline):
                    # A quantum can end on the same batch that completed the
                    # final page.  Requeue only an actually incomplete job;
                    # the store intentionally rejects requeueing completed
                    # jobs so they proceed directly to finalization.
                    if current.completed_count >= current.expected_count:
                        break
                    await self._store.requeue_incomplete(
                        tenant_id=tenant_id,
                        job_id=job.job_id,
                        worker_id=worker_id,
                        requeued_at=datetime.now(UTC),
                    )
                    return True
                processed_batch = await self._process_item_batch(
                    job,
                    worker_id=worker_id,
                    heartbeat=heartbeat,
                    progress=progress,
                    limit=remaining_pages,
                )
                if processed_batch == 0:
                    promote = getattr(self._store, "promote_duplicate_fallbacks", None)
                    if promote is not None:
                        promoted = await promote(
                            tenant_id=job.tenant_id,
                            job_id=job.job_id,
                            worker_id=worker_id,
                        )
                        if promoted:
                            progress.mark()
                            continue
                    current = await self._store.get(tenant_id=tenant_id, job_id=job.job_id)
                    if current is None:
                        raise RuntimeError("website sync job disappeared during page processing")
                    if current.completed_count < current.expected_count:
                        await self._store.requeue_incomplete(
                            tenant_id=tenant_id,
                            job_id=job.job_id,
                            worker_id=worker_id,
                            requeued_at=datetime.now(UTC),
                        )
                        return True
                    break
                processed_pages += processed_batch

            _raise_if_lease_monitor_failed(heartbeat)
            async with self._execution_budget.finalization():
                progress.allow_stall_for(self._finalization_timeout_seconds)
                try:
                    async with asyncio.timeout(self._finalization_timeout_seconds):
                        publication_activated = await self._finalize_job(
                            job,
                            worker_id=worker_id,
                            job_policy=job_policy,
                            heartbeat=heartbeat,
                            progress=progress,
                        )
                except TimeoutError:
                    report = await self._store.aggregate_report(
                        tenant_id=job.tenant_id,
                        job_id=job.job_id,
                    )
                    blocked_at = datetime.now(UTC)
                    await self._store.block(
                        tenant_id=job.tenant_id,
                        job_id=job.job_id,
                        worker_id=worker_id,
                        report=replace(
                            report,
                            errors={
                                **report.errors,
                                "finalization": "website sync finalization timed out",
                            },
                        ),
                        blocked_at=blocked_at,
                        retention_expires_at=blocked_at + self._staging_retention,
                    )
                    return True
        except asyncio.CancelledError:
            raise
        except _LeaseMonitorStopped:
            # Do not mark a stalled production job terminal or abort its
            # snapshot here.  The lease is allowed to expire so another
            # worker can reclaim the durable checkpoints and continue.
            return True
        except Exception as exc:
            if job.mode is WebSyncMode.PRODUCTION and staged_started and not publication_activated:
                try:
                    await self._synchronizer.abort_staged_sync(
                        _job_policy(job),
                        snapshot_id=job.job_id,
                        errors={"fatal": _safe_error_message(exc)},
                        error_code=type(exc).__name__,
                    )
                except Exception:
                    pass
            if publication_activated:
                return True
            try:
                await self._store.fail(
                    tenant_id=tenant_id,
                    job_id=job.job_id,
                    worker_id=worker_id,
                    error_code=type(exc).__name__,
                    error_message=_safe_error_message(exc),
                    failed_at=datetime.now(UTC),
                )
            except RuntimeError:
                # Another worker owns an expired lease; only that worker may finalize the job.
                pass
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
        return True

    async def _cleanup_expired_job(
        self,
        job: WebSyncJob,
        *,
        worker_id: str,
        heartbeat: asyncio.Task[None],
        progress: "_ProgressTracker",
    ) -> bool:
        progress.allow_stall_for(self._finalization_timeout_seconds)
        try:
            async with asyncio.timeout(self._finalization_timeout_seconds):
                await self._synchronizer.abort_staged_sync(
                    _job_policy(job),
                    snapshot_id=job.job_id,
                    errors={"cleanup": "worker lease retry limit was reached"},
                    error_code="staging_cleanup_required",
                )
                _raise_if_lease_monitor_failed(heartbeat)
                await self._store.fail(
                    tenant_id=job.tenant_id,
                    job_id=job.job_id,
                    worker_id=worker_id,
                    error_code="staging_cleanup_completed",
                    error_message="external staging was aborted after lease exhaustion",
                    failed_at=datetime.now(UTC),
                )
        except _LeaseMonitorStopped:
            return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            release_cleanup = getattr(self._store, "release_cleanup", None)
            if release_cleanup is not None:
                await release_cleanup(
                    tenant_id=job.tenant_id,
                    job_id=job.job_id,
                    worker_id=worker_id,
                    available_at=datetime.now(UTC) + timedelta(seconds=self._lease_seconds),
                    error_message=_safe_error_message(exc),
                )
        return True

    async def _finalize_job(
        self,
        job: WebSyncJob,
        *,
        worker_id: str,
        job_policy: WebCrawlPolicy,
        heartbeat: asyncio.Task[None],
        progress: "_ProgressTracker",
    ) -> bool:
        _raise_if_lease_monitor_failed(heartbeat)
        await self._store.start_finalization(
            tenant_id=job.tenant_id,
            job_id=job.job_id,
            worker_id=worker_id,
            started_at=datetime.now(UTC),
        )
        progress.mark()
        inventory = await self._finalization_inventory(
            job,
            heartbeat=heartbeat,
            progress=progress,
        )
        if inventory.item_count != job.expected_count or inventory.has_nonterminal:
            raise RuntimeError("page checkpoint reconciliation failed; the job remains unpublished")
        report = await self._store.aggregate_report(
            tenant_id=job.tenant_id,
            job_id=job.job_id,
        )
        progress.mark()
        duplicate_unresolved = report.duplicate_product_unresolved_count
        if not hasattr(self._store, "apply_duplicate_policy"):
            duplicate_unresolved = report.duplicate_product_count
        if job.mode is WebSyncMode.PRODUCTION and duplicate_unresolved:
            report = replace(
                report,
                blocking_issue_count=max(
                    report.blocking_issue_count,
                    duplicate_unresolved,
                ),
                publication_block_reasons=tuple(
                    dict.fromkeys(
                        (
                            *report.publication_block_reasons,
                            "unresolved_product_identity",
                        )
                    )
                ),
                errors={
                    **report.errors,
                    "product_identity_gate": (
                        f"{duplicate_unresolved} duplicate product identities "
                        "must be resolved before publication"
                    ),
                },
            )
        if job.mode is WebSyncMode.PRODUCTION and (
            report.failed_count or report.blocking_issue_count
        ):
            blocked_at = datetime.now(UTC)
            await self._store.block(
                tenant_id=job.tenant_id,
                job_id=job.job_id,
                worker_id=worker_id,
                report=report,
                blocked_at=blocked_at,
                retention_expires_at=blocked_at + self._staging_retention,
            )
            return False
        current = await self._store.get(tenant_id=job.tenant_id, job_id=job.job_id)
        if current is None:
            raise RuntimeError("website sync job disappeared during finalization")
        if current.cancel_requested_at is not None:
            if job.mode is WebSyncMode.PRODUCTION:
                await self._synchronizer.abort_staged_sync(
                    job_policy,
                    snapshot_id=job.job_id,
                    errors={"canceled": "production synchronization was canceled"},
                    error_code="canceled",
                )
            await self._store.cancel(
                tenant_id=job.tenant_id,
                job_id=job.job_id,
                worker_id=worker_id,
                canceled_at=datetime.now(UTC),
            )
            return False

        publication_activated = False
        if job.mode is WebSyncMode.PRODUCTION:
            _raise_if_lease_monitor_failed(heartbeat)
            try:
                activation = await self._synchronizer.publish_staged_sync(
                    job_policy,
                    snapshot_id=job.job_id,
                    active_version_ids=inventory.active_version_ids,
                    staged_version_ids=inventory.staged_version_ids,
                    expected_chunk_count=report.indexed_chunk_count,
                    expected_product_count=report.product_count,
                    discovered_count=report.discovered_count,
                    errors=report.errors,
                )
            except Exception as exc:  # noqa: BLE001
                blocked_at = datetime.now(UTC)
                error_detail = _safe_error_message(exc)
                block_reasons = report.publication_block_reasons
                blocking_issue_count = report.blocking_issue_count
                if error_detail.startswith("stale_version_reference"):
                    contexts = tuple(
                        reference
                        for reference in inventory.version_references
                        if f"version_id={reference.version_id}" in error_detail
                    )
                    if contexts:
                        context_detail = "; ".join(
                            f"ordinal={reference.ordinal},url={reference.url},"
                            f"version_id={reference.version_id},expected_snapshot={job.job_id}"
                            for reference in contexts[:20]
                        )
                        error_detail = f"{error_detail}; affected_items: {context_detail}"[:1000]
                    block_reasons = tuple(
                        dict.fromkeys((*block_reasons, "stale_version_reference"))
                    )
                    blocking_issue_count = max(1, blocking_issue_count)
                await self._store.block(
                    tenant_id=job.tenant_id,
                    job_id=job.job_id,
                    worker_id=worker_id,
                    report=replace(
                        report,
                        errors={**report.errors, "finalization": error_detail},
                        publication_block_reasons=block_reasons,
                        blocking_issue_count=blocking_issue_count,
                    ),
                    blocked_at=blocked_at,
                    retention_expires_at=blocked_at + self._staging_retention,
                )
                return False
            publication_activated = True
            progress.mark()
            report = replace(
                report,
                published=True,
                product_count=activation.activated_count,
                pending_removal_count=activation.pending_removal_count,
                expired_count=activation.expired_count,
            )
        try:
            if job.mode is WebSyncMode.PRODUCTION:
                _raise_if_lease_monitor_failed(heartbeat)
                await self._manifest_store.replace_page_states(
                    tenant_id=job.tenant_id,
                    site_id=job.site_id,
                    states=inventory.page_states,
                )
                progress.mark()
            _raise_if_lease_monitor_failed(heartbeat)
            await self._store.complete(
                tenant_id=job.tenant_id,
                job_id=job.job_id,
                worker_id=worker_id,
                report=report,
                completed_at=datetime.now(UTC),
            )
            progress.mark()
        except Exception:
            if publication_activated:
                # Publication is an irreversible boundary. A later claim will
                # reconcile the idempotent bookkeeping without aborting it.
                return True
            raise
        return publication_activated

    async def _prepare_job(
        self,
        job: WebSyncJob,
        *,
        worker_id: str,
        max_pages: int | None,
        deadline: float | None,
        heartbeat: asyncio.Task[None],
        progress: "_ProgressTracker",
    ) -> bool:
        manifest = await self._manifest_store.get_metadata(
            tenant_id=job.tenant_id,
            site_id=job.site_id,
            manifest_id=job.manifest_id,
        )
        if manifest is None:
            raise RuntimeError("immutable website crawl manifest was not found")
        if manifest.fingerprint != job.manifest_fingerprint:
            raise RuntimeError("immutable manifest fingerprint changed before preparation")
        current = job
        prepared_this_quantum = 0
        stage = getattr(current, "prepare_stage", "copy_manifest_items")
        if stage not in {
            "copy_manifest_items",
            "extract_missing_identities",
            "apply_duplicate_policy",
        }:
            raise RuntimeError(f"unsupported persisted preparation stage: {stage}")
        if stage == "copy_manifest_items":
            while current.prepared_count < current.expected_count:
                remaining_quantum = None if max_pages is None else max_pages - prepared_this_quantum
                if remaining_quantum == 0 or _deadline_reached(deadline):
                    await self._yield_preparation(
                        current,
                        worker_id=worker_id,
                        stage="copy_manifest_items",
                        cursor=current.prepared_count,
                    )
                    return False
                remaining = current.expected_count - current.prepared_count
                manifest_items = await self._manifest_store.list_items(
                    tenant_id=job.tenant_id,
                    site_id=job.site_id,
                    manifest_id=job.manifest_id,
                    offset=current.prepared_count,
                    limit=min(
                        self._prepare_batch_size,
                        remaining,
                        remaining_quantum if remaining_quantum is not None else remaining,
                    ),
                    deterministic_sample=(
                        job.mode is WebSyncMode.SHADOW and job.sample_size is not None
                    ),
                )
                if not manifest_items:
                    raise RuntimeError("immutable manifest ended before the expected page count")
                prepared_items = tuple(
                    WebSyncJobItem(
                        tenant_id=job.tenant_id,
                        job_id=job.job_id,
                        site_id=job.site_id,
                        manifest_id=job.manifest_id,
                        item_id=str(uuid5(NAMESPACE_URL, f"{job.job_id}:{item.url}")),
                        ordinal=current.prepared_count + index,
                        url=item.url,
                        source_sitemap_url=item.source_sitemap_url,
                        content_kind=item.content_kind,
                        document_id=item.document_id,
                        version_id=item.version_id,
                        canonical_url=item.canonical_url,
                        final_url=item.final_url,
                        etag=item.etag,
                        last_modified=item.response_last_modified or item.last_modified,
                        product_key=item.product_key,
                        normalized_product_key=item.normalized_product_key,
                        normalization_version=item.normalization_version,
                    )
                    for index, item in enumerate(manifest_items[:remaining])
                )
                current = await self._store.append_prepared_items(
                    tenant_id=job.tenant_id,
                    job_id=job.job_id,
                    worker_id=worker_id,
                    items=prepared_items,
                )
                progress.mark()
                _raise_if_lease_monitor_failed(heartbeat)
                prepared_this_quantum += len(prepared_items)
            stage = "extract_missing_identities"

        identity_cursor = 0
        if stage == "extract_missing_identities":
            if (max_pages is not None and prepared_this_quantum >= max_pages) or _deadline_reached(
                deadline
            ):
                await self._yield_preparation(
                    current,
                    worker_id=worker_id,
                    stage="extract_missing_identities",
                    cursor=max(0, getattr(current, "prepare_cursor", 0))
                    if getattr(current, "prepare_stage", "copy_manifest_items")
                    == "extract_missing_identities"
                    else 0,
                )
                return False
            identity_budget = (
                None if max_pages is None else max(1, max_pages - prepared_this_quantum)
            )
            identities_complete, identity_cursor = await self._extract_missing_identities(
                current,
                worker_id=worker_id,
                offset=(
                    max(0, getattr(current, "prepare_cursor", 0))
                    if getattr(current, "prepare_stage", "copy_manifest_items")
                    == "extract_missing_identities"
                    else 0
                ),
                max_items=identity_budget,
                deadline=deadline,
                heartbeat=heartbeat,
                progress=progress,
            )
            if not identities_complete:
                await self._yield_preparation(
                    current,
                    worker_id=worker_id,
                    stage="extract_missing_identities",
                    cursor=identity_cursor,
                )
                return False
            stage = "apply_duplicate_policy"
        if _deadline_reached(deadline) and stage == "apply_duplicate_policy":
            await self._yield_preparation(
                current,
                worker_id=worker_id,
                stage="apply_duplicate_policy",
                cursor=0
                if getattr(current, "prepare_stage", "") != "apply_duplicate_policy"
                else current.prepare_cursor,
            )
            return False
        if max_pages is not None and prepared_this_quantum >= max_pages:
            await self._yield_preparation(
                current,
                worker_id=worker_id,
                stage="apply_duplicate_policy",
                cursor=(
                    max(0, getattr(current, "prepare_cursor", 0))
                    if getattr(current, "prepare_stage", "") == "apply_duplicate_policy"
                    else 0
                ),
            )
            return False
        apply_policy_batch = getattr(self._store, "apply_duplicate_policy_batch", None)
        if apply_policy_batch is not None:
            _raise_if_lease_monitor_failed(heartbeat)
            batch_limit = self._prepare_batch_size
            if max_pages is not None:
                batch_limit = min(batch_limit, max(1, max_pages - prepared_this_quantum))
            _, next_cursor, complete = await apply_policy_batch(
                tenant_id=job.tenant_id,
                job_id=job.job_id,
                worker_id=worker_id,
                policy=job.policy,
                cursor=(
                    max(0, getattr(current, "prepare_cursor", 0))
                    if getattr(current, "prepare_stage", "") == "apply_duplicate_policy"
                    else 0
                ),
                limit=max(1, batch_limit),
            )
            progress.mark()
            if not complete:
                await self._yield_preparation(
                    current,
                    worker_id=worker_id,
                    stage="apply_duplicate_policy",
                    cursor=next_cursor,
                )
                return False
        else:
            apply_policy = getattr(self._store, "apply_duplicate_policy", None)
            if apply_policy is not None:
                _raise_if_lease_monitor_failed(heartbeat)
                await apply_policy(
                    tenant_id=job.tenant_id,
                    job_id=job.job_id,
                    worker_id=worker_id,
                    policy=job.policy,
                )
                progress.mark()
        _raise_if_lease_monitor_failed(heartbeat)
        await self._store.finish_preparation(
            tenant_id=job.tenant_id,
            job_id=job.job_id,
            worker_id=worker_id,
            manifest_fingerprint=manifest.fingerprint,
        )
        progress.mark()
        return True

    async def _extract_missing_identities(
        self,
        job: WebSyncJob,
        *,
        worker_id: str,
        offset: int = 0,
        max_items: int | None = None,
        deadline: float | None = None,
        heartbeat: asyncio.Task[None],
        progress: "_ProgressTracker",
    ) -> tuple[bool, int]:
        extractor = getattr(self._synchronizer, "extract_identity", None)
        updater = getattr(self._store, "update_item_identity", None)
        if extractor is None or updater is None:
            return True, offset
        processed = 0
        while True:
            if (max_items is not None and processed >= max_items) or _deadline_reached(deadline):
                return False, offset
            batch_limit = min(500, max_items - processed) if max_items is not None else 500
            batch = await self._store.list_items(
                tenant_id=job.tenant_id,
                job_id=job.job_id,
                status=WebSyncJobItemStatus.PENDING,
                limit=batch_limit,
                offset=offset,
            )
            if not batch:
                return True, offset

            async def extract(item: WebSyncJobItem) -> None:
                async with self._execution_budget.page():
                    try:
                        report = await extractor(_crawl_policy(job, item))
                    except Exception:  # noqa: BLE001
                        return
                validator = _validator_item(item, report)
                if validator is not None and (validator.product_key or item.document_id is None):
                    await updater(
                        tenant_id=job.tenant_id,
                        job_id=job.job_id,
                        item_id=item.item_id,
                        worker_id=worker_id,
                        validator=validator,
                    )

            # Advance the persisted cursor only after each bounded checkpoint.
            # This lets a time quantum stop between chunks without skipping
            # unprocessed rows when another tenant is waiting for a turn.
            for start in range(0, len(batch), self._concurrency):
                if _deadline_reached(deadline):
                    return False, offset
                chunk = batch[start : start + self._concurrency]
                candidates = tuple(
                    item
                    for item in chunk
                    if item.product_key is None
                    and (item.content_kind == "product" or item.document_id is None)
                )
                await asyncio.gather(*(extract(item) for item in candidates))
                offset += len(chunk)
                processed += len(chunk)
                progress.mark()
                _raise_if_lease_monitor_failed(heartbeat)
            if len(batch) < batch_limit:
                return True, offset

    async def _yield_preparation(
        self,
        job: WebSyncJob,
        *,
        worker_id: str,
        stage: str,
        cursor: int,
    ) -> None:
        yield_preparation = getattr(self._store, "yield_preparation", None)
        if yield_preparation is None:
            raise RuntimeError("website sync store does not support resumable preparation")
        await yield_preparation(
            tenant_id=job.tenant_id,
            job_id=job.job_id,
            worker_id=worker_id,
            stage=stage,
            cursor=cursor,
            yielded_at=datetime.now(UTC),
        )

    async def _finalization_inventory(
        self,
        job: WebSyncJob,
        *,
        heartbeat: asyncio.Task[None],
        progress: "_ProgressTracker",
    ) -> "_FinalizationInventory":
        item_count = 0
        has_nonterminal = False
        active_version_ids: list[str] = []
        staged_version_ids: list[str] = []
        page_states: list[WebCrawlPageState] = []
        version_references: list[_PublicationVersionReference] = []
        validated_at = datetime.now(UTC)
        offset = 0
        while True:
            batch = await self._store.list_items(
                tenant_id=job.tenant_id,
                job_id=job.job_id,
                status=None,
                limit=1000,
                offset=offset,
            )
            if not batch:
                break
            item_count += len(batch)
            offset += len(batch)
            for item in batch:
                carries_published_content = item.status in {
                    WebSyncJobItemStatus.SUCCEEDED,
                    WebSyncJobItemStatus.NOT_MODIFIED,
                }
                has_nonterminal = has_nonterminal or item.status in {
                    WebSyncJobItemStatus.PENDING,
                    WebSyncJobItemStatus.FETCHING,
                }
                if carries_published_content and item.version_id is not None:
                    active_version_ids.append(item.version_id)
                    version_references.append(
                        _PublicationVersionReference(
                            ordinal=item.ordinal,
                            url=item.url,
                            version_id=item.version_id,
                        )
                    )
                    if item.report is not None and item.report.indexed_chunk_count > 0:
                        staged_version_ids.append(item.version_id)
                if (
                    job.mode is WebSyncMode.PRODUCTION
                    and carries_published_content
                    and item.document_id
                    and item.version_id
                    and item.canonical_url
                ):
                    page_states.append(
                        WebCrawlPageState(
                            tenant_id=job.tenant_id,
                            site_id=job.site_id,
                            url=item.url,
                            document_id=item.document_id,
                            version_id=item.version_id,
                            canonical_url=item.canonical_url,
                            final_url=item.final_url or item.canonical_url,
                            etag=item.etag,
                            last_modified=item.last_modified,
                            product_key=item.product_key,
                            normalized_product_key=item.normalized_product_key,
                            normalization_version=item.normalization_version,
                            artifact_status="published",
                            validated_at=validated_at,
                        )
                    )
            progress.mark()
            _raise_if_lease_monitor_failed(heartbeat)
            if len(batch) < 1000:
                break
        return _FinalizationInventory(
            item_count=item_count,
            has_nonterminal=has_nonterminal,
            active_version_ids=tuple(active_version_ids),
            staged_version_ids=tuple(staged_version_ids),
            page_states=tuple(page_states),
            version_references=tuple(version_references),
        )

    async def _process_item_batch(
        self,
        job: WebSyncJob,
        *,
        worker_id: str,
        heartbeat: asyncio.Task[None],
        progress: "_ProgressTracker",
        limit: int | None = None,
    ) -> int:
        batch_size = self._concurrency if limit is None else min(self._concurrency, limit)

        async def claim_and_process() -> bool:
            # Each page starts as soon as it owns global capacity. Waiting to
            # assemble a full tenant batch can deadlock when several tenants
            # each hold only part of the remaining process-wide slots.
            lease = await self._execution_budget.acquire_page()
            try:
                claimed_at = datetime.now(UTC)
                item = await self._store.claim_next_item(
                    tenant_id=job.tenant_id,
                    job_id=job.job_id,
                    worker_id=worker_id,
                    claimed_at=claimed_at,
                    lease_expires_at=claimed_at + timedelta(seconds=self._lease_seconds),
                )
                if item is None:
                    return False
                await self._process_item(
                    job,
                    item,
                    worker_id=worker_id,
                    heartbeat=heartbeat,
                    page_lease=lease,
                    progress=progress,
                )
                return True
            finally:
                lease.release()

        tasks = tuple(asyncio.create_task(claim_and_process()) for _ in range(batch_size))
        try:
            results = await asyncio.gather(*tasks)
            return sum(results)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _process_item(
        self,
        job: WebSyncJob,
        item: WebSyncJobItem,
        *,
        worker_id: str,
        heartbeat: asyncio.Task[None],
        page_lease: WebSyncPageLease,
        progress: "_ProgressTracker",
    ) -> None:
        async with page_lease:
            started = perf_counter()
            crawl_item = item
            reusable_checker = getattr(self._synchronizer, "is_reusable_version", None)
            reusable = True
            if item.version_id and reusable_checker is not None:
                reusable = await reusable_checker(
                    _crawl_policy(job, replace(item, version_id=None)),
                    version_id=item.version_id,
                    publication_id=job.job_id,
                )
            if item.version_id and not reusable:
                # A validator is only a performance hint. Never send a stale
                # or discarded version into the crawler's 304/not-modified path.
                crawl_item = replace(item, version_id=None)
            policy = _crawl_policy(job, crawl_item)
            synchronization = asyncio.create_task(
                self._synchronizer.validate(policy)
                if job.mode is WebSyncMode.SHADOW
                else self._synchronizer.stage_page(policy, snapshot_id=job.job_id)
            )
            try:
                done, _pending = await asyncio.wait(
                    {synchronization, heartbeat},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if heartbeat in done:
                    synchronization.cancel()
                    await asyncio.gather(synchronization, return_exceptions=True)
                    heartbeat.result()
                    raise RuntimeError("website sync job lease monitor stopped unexpectedly")
                report = synchronization.result()
                if report.failed_count:
                    detail = next(iter(report.errors.values()), "page validation failed")
                    raise RuntimeError(detail)
                validator = _validator_item(item, report)
                await self._store.complete_item(
                    tenant_id=job.tenant_id,
                    job_id=job.job_id,
                    item_id=item.item_id,
                    worker_id=worker_id,
                    status=_item_status(report),
                    report=_to_job_report(report),
                    validator=validator,
                    duration_ms=int((perf_counter() - started) * 1000),
                    completed_at=datetime.now(UTC),
                )
                progress.mark()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if heartbeat.done():
                    raise
                retryable = _is_retryable_failure(exc)
                await self._store.fail_item(
                    tenant_id=job.tenant_id,
                    job_id=job.job_id,
                    item_id=item.item_id,
                    worker_id=worker_id,
                    error_code=_failure_error_code(exc),
                    error_message=_safe_error_message(exc),
                    duration_ms=int((perf_counter() - started) * 1000),
                    failed_at=datetime.now(UTC),
                    next_attempt_at=_next_attempt_at(item, exc) if retryable else None,
                    terminal=not retryable,
                )
                progress.mark()
            finally:
                synchronization.cancel()
                await asyncio.gather(synchronization, return_exceptions=True)

    async def _heartbeat(
        self,
        *,
        tenant_id: str,
        job_id: str,
        worker_id: str,
        progress: "_ProgressTracker",
    ) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_seconds)
            if progress.is_stalled():
                raise _LeaseMonitorStopped(
                    "website sync job exceeded the no-progress heartbeat limit"
                )
            heartbeat_at = datetime.now(UTC)
            owned = await self._store.heartbeat(
                tenant_id=tenant_id,
                job_id=job_id,
                worker_id=worker_id,
                heartbeat_at=heartbeat_at,
                lease_expires_at=heartbeat_at + timedelta(seconds=self._lease_seconds),
            )
            if not owned:
                raise _LeaseMonitorStopped("website sync job lease was lost")


class _ProgressTracker:
    __slots__ = ("_last_progress", "_stall_limit_seconds")

    def __init__(self, stall_limit_seconds: float) -> None:
        self._last_progress = monotonic()
        self._stall_limit_seconds = stall_limit_seconds

    def mark(self) -> None:
        self._last_progress = monotonic()

    def allow_stall_for(self, seconds: float) -> None:
        self._stall_limit_seconds = max(self._stall_limit_seconds, seconds)

    def is_stalled(self) -> bool:
        return monotonic() - self._last_progress >= self._stall_limit_seconds


def _raise_if_lease_monitor_failed(heartbeat: asyncio.Task[None]) -> None:
    if heartbeat.done():
        heartbeat.result()


def _crawl_policy(job: WebSyncJob, item: WebSyncJobItem) -> WebCrawlPolicy:
    policy = job.policy
    return WebCrawlPolicy(
        tenant_id=job.tenant_id,
        site_id=job.site_id,
        base_url=job.base_url,
        seed_urls=(item.url,),
        sitemap_urls=policy.sitemap_urls,
        max_pages=1,
        max_sitemaps=policy.max_sitemaps,
        max_response_bytes=policy.max_response_bytes,
        max_decompressed_response_bytes=policy.max_decompressed_response_bytes,
        max_compression_ratio=policy.max_compression_ratio,
        request_timeout_seconds=policy.request_timeout_seconds,
        crawl_delay_seconds=policy.crawl_delay_seconds,
        follow_internal_links=policy.follow_internal_links,
        respect_robots_txt=policy.respect_robots_txt,
        content_kind=item.content_kind,
        batch_size=policy.batch_size,
        discover_sitemaps=policy.discover_sitemaps,
        language=policy.primary_language,
        translated_locales=policy.translated_locales,
        enforce_primary_language=True,
        validators=(
            WebCrawlValidator(
                document_id=item.document_id,
                version_id=item.version_id,
                canonical_url=item.canonical_url,
                requested_url=item.url,
                final_url=item.final_url or item.canonical_url,
                etag=item.etag,
                last_modified=item.last_modified,
                product_key=item.product_key,
                normalized_product_key=item.normalized_product_key,
                normalization_version=item.normalization_version,
            ),
        )
        if item.document_id
        and item.version_id
        and item.canonical_url
        and (item.final_url or item.canonical_url)
        else (),
    )


def _job_policy(job: WebSyncJob) -> WebCrawlPolicy:
    policy = job.policy
    return WebCrawlPolicy(
        tenant_id=job.tenant_id,
        site_id=job.site_id,
        base_url=job.base_url,
        max_pages=max(1, job.expected_count),
        max_sitemaps=policy.max_sitemaps,
        max_response_bytes=policy.max_response_bytes,
        max_decompressed_response_bytes=policy.max_decompressed_response_bytes,
        max_compression_ratio=policy.max_compression_ratio,
        request_timeout_seconds=policy.request_timeout_seconds,
        crawl_delay_seconds=policy.crawl_delay_seconds,
        follow_internal_links=False,
        respect_robots_txt=policy.respect_robots_txt,
        batch_size=policy.batch_size,
        discover_sitemaps=False,
        language=policy.primary_language,
        translated_locales=policy.translated_locales,
        enforce_primary_language=True,
    )


def _validator_item(
    item: WebSyncJobItem,
    report: WebKnowledgeSyncReport,
) -> WebSyncJobItem | None:
    if not report.validators:
        return None
    validator = report.validators[0]
    return WebSyncJobItem(
        tenant_id=item.tenant_id,
        job_id=item.job_id,
        site_id=item.site_id,
        manifest_id=item.manifest_id,
        item_id=item.item_id,
        ordinal=item.ordinal,
        url=item.url,
        source_sitemap_url=item.source_sitemap_url,
        content_kind=item.content_kind,
        document_id=validator.document_id,
        version_id=validator.version_id,
        canonical_url=validator.canonical_url,
        final_url=validator.final_url,
        etag=validator.etag,
        last_modified=validator.last_modified,
        product_key=validator.product_key,
        normalized_product_key=validator.normalized_product_key,
        normalization_version=validator.normalization_version,
    )


def _item_status(report: WebKnowledgeSyncReport) -> WebSyncJobItemStatus:
    if report.excluded_count and not report.indexed_count:
        return WebSyncJobItemStatus.EXCLUDED
    if report.http_not_modified_count or (
        report.document_count
        and not report.changed_document_count
        and not report.indexed_count
        and report.validators
    ):
        return WebSyncJobItemStatus.NOT_MODIFIED
    return WebSyncJobItemStatus.SUCCEEDED


@dataclass(frozen=True, slots=True)
class _FinalizationInventory:
    item_count: int
    has_nonterminal: bool
    active_version_ids: tuple[str, ...]
    staged_version_ids: tuple[str, ...]
    page_states: tuple[WebCrawlPageState, ...]
    version_references: tuple["_PublicationVersionReference", ...]


@dataclass(frozen=True, slots=True)
class _PublicationVersionReference:
    ordinal: int
    url: str
    version_id: str


def _to_job_report(report: WebKnowledgeSyncReport) -> WebSyncJobReport:
    return WebSyncJobReport(
        pipeline_sync_job_id=report.sync_job_id,
        published=report.published,
        discovered_count=report.discovered_count,
        document_count=report.document_count,
        changed_document_count=report.changed_document_count,
        unchanged_document_count=report.skipped_count,
        http_not_modified_count=report.http_not_modified_count,
        duplicate_count=report.duplicate_count,
        duplicate_product_count=report.duplicate_product_count,
        duplicate_product_total=getattr(report, "duplicate_product_total", 0),
        duplicate_product_excluded_count=getattr(report, "duplicate_product_excluded_count", 0),
        duplicate_product_conflict_warning_count=getattr(
            report, "duplicate_product_conflict_warning_count", 0
        ),
        duplicate_product_unresolved_count=getattr(report, "duplicate_product_unresolved_count", 0),
        winner_product_count=getattr(report, "winner_product_count", 0),
        product_count=report.product_count,
        pending_removal_count=report.pending_removal_count,
        expired_count=report.expired_count,
        indexed_chunk_count=report.indexed_count,
        excluded_count=report.excluded_count,
        failed_count=report.failed_count,
        errors=dict(report.errors),
    )


def _safe_error_message(exc: Exception) -> str:
    message = " ".join(str(exc).split())
    return (message or "website synchronization failed")[:1000]


def _next_attempt_at(item: WebSyncJobItem, exc: Exception) -> datetime | None:
    if not _is_retryable_failure(exc):
        return None
    retry_after = exc.retry_after_seconds if isinstance(exc, RetryableWebFetchError) else None
    delay = (
        retry_after
        if retry_after is not None
        else min(300, 5 * (2 ** max(0, item.attempt_count - 1)))
    )
    jitter = int(sha256(item.url.encode("utf-8")).hexdigest()[:2], 16) % 5
    return datetime.now(UTC) + timedelta(seconds=delay + jitter)


def _is_retryable_failure(exc: Exception) -> bool:
    message = str(exc).casefold()
    return isinstance(exc, RetryableWebFetchError) or any(
        marker in message
        for marker in ("http 429", "http 503", "web fetch failed", "timeout", "connection")
    )


def _failure_error_code(exc: Exception) -> str:
    if isinstance(exc, KnowledgeVersionSchemaInvariantError):
        return "knowledge_version_schema_invariant_violation"
    message = str(exc).casefold()
    if "staging_cleanup_required" in message:
        return "staging_cleanup_required"
    if "knowledge_chunk_heading_too_long" in message:
        return "knowledge_chunk_heading_too_long"
    if "knowledge_chunk_" in message and "_too_long" in message:
        return "knowledge_chunk_metadata_too_long"
    if "stringdatarighttruncationerror" in message:
        return "database_value_too_long"
    return type(exc).__name__


def _deadline_reached(deadline: float | None) -> bool:
    return deadline is not None and perf_counter() >= deadline
