import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import md5

import pytest

from app.application.dto import (
    EnqueueWebSyncJobCommand,
    ListWebSyncJobsQuery,
    ReconcileStaleWebSyncJobCommand,
    RetryWebSyncJobCommand,
)
from app.application.services import WebSyncJobService
from app.domain.models import (
    AuthenticatedPrincipal,
    ProductSnapshotActivation,
    WebCrawlManifest,
    WebCrawlManifestItem,
    WebCrawlManifestStatus,
    WebSyncJob,
    WebSyncJobItem,
    WebSyncJobItemStatus,
    WebSyncJobPhase,
    WebSyncJobReport,
    WebSyncJobStatus,
    WebSyncMode,
    WebSyncPolicySnapshot,
    WebSyncPublicationStatus,
)
from app.domain.ports.web_sync_jobs import WebSyncSourceConfigVersionConflictError
from app.knowledge.web import WebCrawlValidator, WebKnowledgeSyncReport, WebSyncJobWorker
from app.knowledge.web.errors import RetryableWebFetchError
from app.knowledge.web.execution_budget import WebSyncExecutionBudget
from tests.fakes.adapters import InMemoryWebCrawlManifestStore


class InMemoryJobStore:
    def __init__(self, *, source_config_version: int = 0) -> None:
        self.jobs: dict[str, WebSyncJob] = {}
        self.items: dict[str, list[WebSyncJobItem]] = {}
        self.prepared_batches: list[int] = []
        self.source_config_version = source_config_version

    async def enqueue(
        self,
        job: WebSyncJob,
        items: tuple[WebSyncJobItem, ...] = (),
        *,
        expected_source_config_version: int = 0,
    ) -> tuple[WebSyncJob, bool]:
        if expected_source_config_version != self.source_config_version:
            raise WebSyncSourceConfigVersionConflictError
        active = next(
            (
                item
                for item in self.jobs.values()
                if item.tenant_id == job.tenant_id
                and item.site_id == job.site_id
                and item.status
                in {
                    WebSyncJobStatus.PREPARING,
                    WebSyncJobStatus.QUEUED,
                    WebSyncJobStatus.RUNNING,
                    WebSyncJobStatus.BLOCKED,
                }
            ),
            None,
        )
        if active is not None:
            return active, False
        self.jobs[job.job_id] = job
        self.items[job.job_id] = list(items)
        return job, True

    async def get(self, *, tenant_id: str, job_id: str) -> WebSyncJob | None:
        job = self.jobs.get(job_id)
        return job if job is not None and job.tenant_id == tenant_id else None

    async def append_prepared_items(self, **kwargs) -> WebSyncJob:  # type: ignore[no-untyped-def]
        job = self.jobs[kwargs["job_id"]]
        self.prepared_batches.append(len(kwargs["items"]))
        existing = {item.url: item for item in self.items[job.job_id]}
        existing.update({item.url: item for item in kwargs["items"]})
        self.items[job.job_id] = sorted(existing.values(), key=lambda item: item.ordinal)
        prepared_count = len(self.items[job.job_id])
        prepared = replace(
            job,
            prepared_count=prepared_count,
            prepare_cursor=prepared_count,
        )
        self.jobs[job.job_id] = prepared
        return prepared

    async def update_item_identity(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        items = self.items[kwargs["job_id"]]
        index = next(i for i, item in enumerate(items) if item.item_id == kwargs["item_id"])
        item = items[index]
        validator = kwargs["validator"]
        items[index] = replace(
            item,
            document_id=validator.document_id,
            canonical_url=validator.canonical_url,
            final_url=validator.final_url,
            etag=validator.etag,
            last_modified=validator.last_modified,
            product_key=validator.product_key,
            normalized_product_key=validator.normalized_product_key,
            normalization_version=validator.normalization_version,
        )

    async def finish_preparation(self, **kwargs) -> WebSyncJob:  # type: ignore[no-untyped-def]
        job = self.jobs[kwargs["job_id"]]
        if job.manifest_fingerprint != kwargs["manifest_fingerprint"]:
            raise RuntimeError("immutable manifest fingerprint changed during preparation")
        if job.prepared_count != job.expected_count:
            raise RuntimeError("prepared page count does not match the immutable manifest scope")
        queued = replace(
            job,
            status=WebSyncJobStatus.QUEUED,
            phase=WebSyncJobPhase.QUEUED,
            lease_owner=None,
            lease_expires_at=None,
            attempt_count=0,
        )
        self.jobs[job.job_id] = queued
        return queued

    async def yield_preparation(self, **kwargs) -> WebSyncJob:  # type: ignore[no-untyped-def]
        job = self.jobs[kwargs["job_id"]]
        yielded = replace(
            job,
            prepare_stage=kwargs["stage"],
            prepare_cursor=kwargs["cursor"],
            lease_owner=None,
            lease_expires_at=None,
            available_at=kwargs["yielded_at"],
            yield_count=job.yield_count + 1,
        )
        self.jobs[job.job_id] = yielded
        return yielded

    async def requeue_incomplete(self, **kwargs) -> WebSyncJob:  # type: ignore[no-untyped-def]
        job = self.jobs[kwargs["job_id"]]
        queued = replace(
            job,
            status=WebSyncJobStatus.QUEUED,
            phase=WebSyncJobPhase.QUEUED,
            lease_owner=None,
            lease_expires_at=None,
        )
        self.jobs[job.job_id] = queued
        return queued

    async def list_recent(
        self, *, tenant_id: str, site_id: str | None, limit: int
    ) -> tuple[WebSyncJob, ...]:
        return tuple(
            item
            for item in reversed(tuple(self.jobs.values()))
            if item.tenant_id == tenant_id and (site_id is None or item.site_id == site_id)
        )[:limit]

    async def claim_next(self, **kwargs) -> WebSyncJob | None:  # type: ignore[no-untyped-def]
        for job_id, job in self.jobs.items():
            cleanup_blocked = job.status is WebSyncJobStatus.BLOCKED and (
                job.cancel_requested_at is not None
                or (
                    job.retention_expires_at is not None
                    and job.retention_expires_at <= kwargs["claimed_at"]
                )
            )
            due_item = any(
                item.status is WebSyncJobItemStatus.PENDING
                and (item.next_attempt_at is None or item.next_attempt_at <= kwargs["claimed_at"])
                for item in self.items[job_id]
            )
            queued_claimable = job.status is WebSyncJobStatus.QUEUED and (
                job.phase is WebSyncJobPhase.FINALIZING
                or job.completed_count >= job.expected_count
                or due_item
            )
            if job.tenant_id == kwargs["tenant_id"] and (
                job.status is WebSyncJobStatus.PREPARING or queued_claimable or cleanup_blocked
            ):
                preparing = job.status is WebSyncJobStatus.PREPARING
                claimed = replace(
                    job,
                    status=(WebSyncJobStatus.PREPARING if preparing else WebSyncJobStatus.RUNNING),
                    phase=(
                        WebSyncJobPhase.PREPARING
                        if preparing
                        else WebSyncJobPhase.AWAITING_REMEDIATION
                        if cleanup_blocked
                        else WebSyncJobPhase.PROCESSING
                    ),
                    attempt_count=job.attempt_count + 1,
                    lease_owner=kwargs["worker_id"],
                    lease_expires_at=kwargs["lease_expires_at"],
                    started_at=kwargs["claimed_at"],
                )
                self.jobs[job_id] = claimed
                return claimed
        return None

    async def heartbeat(self, **_kwargs) -> bool:  # type: ignore[no-untyped-def]
        return True

    async def request_cancel(self, **kwargs) -> WebSyncJob:  # type: ignore[no-untyped-def]
        job = self.jobs[kwargs["job_id"]]
        canceled = replace(job, cancel_requested_at=kwargs["requested_at"])
        self.jobs[job.job_id] = canceled
        return canceled

    async def block(self, **kwargs) -> WebSyncJob:  # type: ignore[no-untyped-def]
        job = self.jobs[kwargs["job_id"]]
        blocked = replace(
            job,
            status=WebSyncJobStatus.BLOCKED,
            phase=WebSyncJobPhase.AWAITING_REMEDIATION,
            publication_status=WebSyncPublicationStatus.PENDING,
            report=kwargs["report"],
            blocked_at=kwargs["blocked_at"],
            retention_expires_at=kwargs["retention_expires_at"],
            error_code="remediation_required",
            error_message="staged snapshot is retained for failed-page remediation",
            lease_owner=None,
            lease_expires_at=None,
        )
        self.jobs[job.job_id] = blocked
        return blocked

    async def retry_blocked(self, **kwargs) -> WebSyncJob:  # type: ignore[no-untyped-def]
        job = self.jobs[kwargs["job_id"]]
        if job.status is not WebSyncJobStatus.BLOCKED:
            raise ValueError("only blocked website sync jobs can be retried")
        if job.retention_expires_at is None or job.retention_expires_at <= kwargs["requested_at"]:
            raise ValueError("staged snapshot retention has expired")
        retry_count = 0
        failed_count = 0
        next_items: list[WebSyncJobItem] = []
        for item in self.items[job.job_id]:
            retryable = item.status is WebSyncJobItemStatus.FAILED
            if not retryable:
                next_items.append(item)
                continue
            retry_count += 1
            failed_count += int(item.status is WebSyncJobItemStatus.FAILED)
            next_items.append(
                replace(
                    item,
                    status=WebSyncJobItemStatus.PENDING,
                    attempt_count=0,
                    completed_at=None,
                    duration_ms=None,
                    report=None,
                    error_code=None,
                    error_message=None,
                )
            )
        finalization_only = (
            retry_count == 0
            and job.report is not None
            and (job.report.failed_count == 0 and "finalization" in job.report.errors)
        )
        if retry_count == 0 and not finalization_only:
            raise ValueError("blocked website sync job has no retryable pages")
        self.items[job.job_id] = next_items
        retried = replace(
            job,
            status=WebSyncJobStatus.QUEUED,
            phase=(WebSyncJobPhase.FINALIZING if finalization_only else WebSyncJobPhase.QUEUED),
            completed_count=job.completed_count - retry_count,
            failed_item_count=job.failed_item_count - failed_count,
            report=None,
            error_code=None,
            error_message=None,
            cancel_requested_at=None,
            blocked_at=None,
            retention_expires_at=None,
            attempt_count=0,
        )
        self.jobs[job.job_id] = retried
        return retried

    async def list_referenced_version_ids(self, **kwargs) -> tuple[str, ...]:  # type: ignore[no-untyped-def]
        return tuple(
            dict.fromkeys(
                item.version_id
                for item in self.items[kwargs["job_id"]]
                if item.version_id is not None
            )
        )

    async def reconcile_stale_versions(self, **kwargs) -> WebSyncJob:  # type: ignore[no-untyped-def]
        job = self.jobs[kwargs["job_id"]]
        targets = set(kwargs["version_ids"])
        replaced_items: list[WebSyncJobItem] = []
        succeeded_count = 0
        not_modified_count = 0
        for item in self.items[job.job_id]:
            if item.version_id not in targets or item.status not in {
                WebSyncJobItemStatus.SUCCEEDED,
                WebSyncJobItemStatus.NOT_MODIFIED,
            }:
                replaced_items.append(item)
                continue
            succeeded_count += int(item.status is WebSyncJobItemStatus.SUCCEEDED)
            not_modified_count += int(item.status is WebSyncJobItemStatus.NOT_MODIFIED)
            replaced_items.append(
                replace(
                    item,
                    status=WebSyncJobItemStatus.PENDING,
                    attempt_count=0,
                    version_id=None,
                    etag=None,
                    last_modified=None,
                    completed_at=None,
                    duration_ms=None,
                    report=None,
                    error_code=None,
                    error_message=None,
                    outcome_reason=None,
                )
            )
        count = succeeded_count + not_modified_count
        if not count:
            raise ValueError("stale version references are no longer attached to the job")
        self.items[job.job_id] = replaced_items
        reconciled = replace(
            job,
            status=WebSyncJobStatus.QUEUED,
            phase=WebSyncJobPhase.QUEUED,
            completed_count=job.completed_count - count,
            succeeded_count=job.succeeded_count - succeeded_count,
            not_modified_count=job.not_modified_count - not_modified_count,
            report=None,
            error_code=None,
            error_message=None,
            blocked_at=None,
            retention_expires_at=None,
            attempt_count=0,
        )
        self.jobs[job.job_id] = reconciled
        return reconciled

    async def cancel(self, **kwargs) -> WebSyncJob:  # type: ignore[no-untyped-def]
        job = self.jobs[kwargs["job_id"]]
        remaining = sum(
            item.status in {WebSyncJobItemStatus.PENDING, WebSyncJobItemStatus.FETCHING}
            for item in self.items[job.job_id]
        )
        self.items[job.job_id] = [
            replace(
                item,
                status=WebSyncJobItemStatus.CANCELED,
                completed_at=kwargs["canceled_at"],
            )
            if item.status in {WebSyncJobItemStatus.PENDING, WebSyncJobItemStatus.FETCHING}
            else item
            for item in self.items[job.job_id]
        ]
        canceled = replace(
            job,
            status=WebSyncJobStatus.CANCELED,
            phase=WebSyncJobPhase.COMPLETED,
            completed_count=job.completed_count + remaining,
            canceled_item_count=job.canceled_item_count + remaining,
            completed_at=kwargs["canceled_at"],
            lease_owner=None,
            lease_expires_at=None,
        )
        self.jobs[job.job_id] = canceled
        return canceled

    async def claim_next_item(self, **kwargs) -> WebSyncJobItem | None:  # type: ignore[no-untyped-def]
        items = self.items[kwargs["job_id"]]
        for index, item in enumerate(items):
            if item.status is WebSyncJobItemStatus.PENDING and (
                item.next_attempt_at is None or item.next_attempt_at <= kwargs["claimed_at"]
            ):
                claimed = replace(
                    item,
                    status=WebSyncJobItemStatus.FETCHING,
                    attempt_count=item.attempt_count + 1,
                    lease_owner=kwargs["worker_id"],
                    lease_expires_at=kwargs["lease_expires_at"],
                    started_at=item.started_at or kwargs["claimed_at"],
                )
                items[index] = claimed
                return claimed
        return None

    async def complete_item(self, **kwargs) -> WebSyncJobItem:  # type: ignore[no-untyped-def]
        items = self.items[kwargs["job_id"]]
        index = next(i for i, item in enumerate(items) if item.item_id == kwargs["item_id"])
        item = items[index]
        validator = kwargs["validator"]
        completed = replace(
            item,
            status=kwargs["status"],
            report=kwargs["report"],
            duration_ms=kwargs["duration_ms"],
            completed_at=kwargs["completed_at"],
            lease_owner=None,
            lease_expires_at=None,
            next_attempt_at=None,
            outcome_reason=_test_outcome_reason(kwargs["status"], kwargs["report"]),
            document_id=validator.document_id if validator else item.document_id,
            version_id=validator.version_id if validator else item.version_id,
            canonical_url=validator.canonical_url if validator else item.canonical_url,
            final_url=validator.final_url if validator else item.final_url,
            etag=validator.etag if validator else item.etag,
            last_modified=validator.last_modified if validator else item.last_modified,
            product_key=validator.product_key if validator else item.product_key,
        )
        items[index] = completed
        job = self.jobs[item.job_id]
        counters = {
            "succeeded_count": job.succeeded_count,
            "not_modified_count": job.not_modified_count,
            "excluded_item_count": job.excluded_item_count,
        }
        key = {
            WebSyncJobItemStatus.SUCCEEDED: "succeeded_count",
            WebSyncJobItemStatus.NOT_MODIFIED: "not_modified_count",
            WebSyncJobItemStatus.EXCLUDED: "excluded_item_count",
        }[kwargs["status"]]
        counters[key] += 1
        self.jobs[item.job_id] = replace(
            job,
            completed_count=job.completed_count + 1,
            **counters,
        )
        return completed

    async def fail_item(self, **kwargs) -> WebSyncJobItem:  # type: ignore[no-untyped-def]
        items = self.items[kwargs["job_id"]]
        index = next(i for i, item in enumerate(items) if item.item_id == kwargs["item_id"])
        item = items[index]
        requested_terminal = kwargs.get("terminal")
        terminal = (
            item.attempt_count >= item.max_attempts
            if requested_terminal is None
            else requested_terminal
        )
        failed = replace(
            item,
            status=(WebSyncJobItemStatus.FAILED if terminal else WebSyncJobItemStatus.PENDING),
            error_code=kwargs["error_code"],
            error_message=kwargs["error_message"],
            lease_owner=None,
            lease_expires_at=None,
            next_attempt_at=kwargs.get("next_attempt_at") if not terminal else None,
        )
        items[index] = failed
        if terminal:
            job = self.jobs[item.job_id]
            self.jobs[item.job_id] = replace(
                job,
                completed_count=job.completed_count + 1,
                failed_item_count=job.failed_item_count + 1,
            )
        return failed

    async def list_items(self, **kwargs) -> tuple[WebSyncJobItem, ...]:  # type: ignore[no-untyped-def]
        items = self.items[kwargs["job_id"]]
        status = kwargs["status"]
        return tuple(item for item in items if status is None or item.status is status)[
            kwargs.get("offset", 0) : kwargs.get("offset", 0) + kwargs["limit"]
        ]

    async def aggregate_report(self, **kwargs) -> WebSyncJobReport:  # type: ignore[no-untyped-def]
        items = self.items[kwargs["job_id"]]
        reports = [item.report for item in items if item.report]
        products = [
            item.product_key
            for item in items
            if item.product_key and item.report is not None and item.report.product_count > 0
        ]
        document_count = sum(item.document_count for item in reports)
        failed_page_count = sum(item.status is WebSyncJobItemStatus.FAILED for item in items)
        unresolved_product_identity_count = len(products) - len(set(products))
        return WebSyncJobReport(
            pipeline_sync_job_id=kwargs["job_id"],
            published=False,
            discovered_count=sum(item.discovered_count for item in reports),
            document_count=document_count,
            changed_document_count=sum(item.changed_document_count for item in reports),
            unchanged_document_count=sum(item.unchanged_document_count for item in reports),
            http_not_modified_count=sum(item.http_not_modified_count for item in reports),
            duplicate_count=sum(item.duplicate_count for item in reports),
            duplicate_product_count=unresolved_product_identity_count,
            duplicate_product_unresolved_count=unresolved_product_identity_count,
            product_count=len(set(products)),
            processed_page_count=sum(
                item.status not in {WebSyncJobItemStatus.PENDING, WebSyncJobItemStatus.FETCHING}
                for item in items
            ),
            produced_document_count=document_count,
            failed_page_count=failed_page_count,
            unresolved_product_identity_count=unresolved_product_identity_count,
            pending_removal_count=0,
            expired_count=0,
            indexed_chunk_count=sum(item.indexed_chunk_count for item in reports),
            excluded_count=sum(item.excluded_count for item in reports),
            failed_count=failed_page_count,
            errors={
                item.url: item.error_message or "failed"
                for item in items
                if item.status is WebSyncJobItemStatus.FAILED
            },
        )

    async def start_finalization(self, **kwargs) -> WebSyncJob:  # type: ignore[no-untyped-def]
        job = self.jobs[kwargs["job_id"]]
        finalizing = replace(job, phase=WebSyncJobPhase.FINALIZING)
        self.jobs[job.job_id] = finalizing
        return finalizing

    async def complete(self, **kwargs) -> WebSyncJob:  # type: ignore[no-untyped-def]
        job = self.jobs[kwargs["job_id"]]
        completed = replace(
            job,
            status=(
                WebSyncJobStatus.SUCCEEDED
                if kwargs["report"].published
                or (job.mode is WebSyncMode.SHADOW and kwargs["report"].failed_count == 0)
                else WebSyncJobStatus.FAILED
            ),
            phase=WebSyncJobPhase.COMPLETED,
            publication_status=(
                WebSyncPublicationStatus.NOT_REQUESTED
                if job.mode is WebSyncMode.SHADOW
                else WebSyncPublicationStatus.PUBLISHED
                if kwargs["report"].published
                else WebSyncPublicationStatus.REFUSED
            ),
            report=kwargs["report"],
            completed_at=kwargs["completed_at"],
            lease_owner=None,
            lease_expires_at=None,
        )
        self.jobs[job.job_id] = completed
        return completed

    async def fail(self, **kwargs) -> WebSyncJob:  # type: ignore[no-untyped-def]
        job = self.jobs[kwargs["job_id"]]
        failed = replace(
            job,
            status=WebSyncJobStatus.FAILED,
            error_code=kwargs["error_code"],
            error_message=kwargs["error_message"],
            completed_at=kwargs["failed_at"],
            lease_owner=None,
            lease_expires_at=None,
        )
        self.jobs[job.job_id] = failed
        return failed


def _test_outcome_reason(
    status: WebSyncJobItemStatus,
    report: WebSyncJobReport,
) -> str:
    if status is WebSyncJobItemStatus.SUCCEEDED:
        return "indexed"
    if status is WebSyncJobItemStatus.NOT_MODIFIED:
        return "not_modified"
    reasons = " ".join(report.errors.values()).casefold()
    if "robots_txt_disallow" in reasons:
        return "robots_excluded"
    if "canonical_duplicate" in reasons or "duplicate" in reasons:
        return "canonical_duplicate"
    if "noindex" in reasons:
        return "noindex"
    if any(marker in reasons for marker in ("http 404", "http 410", "http_404", "http_410")):
        return "gone"
    if "approved_" in reasons:
        return "approved_exclusion"
    return "policy_excluded"


class SuccessfulSynchronizer:
    async def validate(self, policy) -> WebKnowledgeSyncReport:  # type: ignore[no-untyped-def]
        url = policy.seed_urls[0]
        return WebKnowledgeSyncReport(
            sync_job_id="pipeline-1",
            discovered_count=1,
            document_count=1,
            indexed_count=0,
            skipped_count=1,
            excluded_count=0,
            failed_count=0,
            errors={},
            published=False,
            product_count=1,
            http_not_modified_count=1,
            validators=(
                WebCrawlValidator(
                    document_id=f"doc:{url}",
                    version_id=f"version:{url}",
                    canonical_url=url,
                    requested_url=url,
                    final_url=url,
                    etag='"etag"',
                    product_key=url.rsplit("/", 1)[-1],
                ),
            ),
        )


class HangingSynchronizer(SuccessfulSynchronizer):
    def __init__(self) -> None:
        self.canceled = asyncio.Event()

    async def validate(self, policy) -> WebKnowledgeSyncReport:  # type: ignore[no-untyped-def]
        try:
            await asyncio.Future()
        finally:
            self.canceled.set()
        raise AssertionError("unreachable")


class IdentityTrackingSynchronizer(SuccessfulSynchronizer):
    def __init__(self) -> None:
        self.identity_urls: list[str] = []

    async def extract_identity(self, policy) -> WebKnowledgeSyncReport:  # type: ignore[no-untyped-def]
        self.identity_urls.append(policy.seed_urls[0])
        return await super().validate(policy)


class RetryAfterSynchronizer(SuccessfulSynchronizer):
    def __init__(self) -> None:
        self.attempts = 0

    async def validate(self, policy) -> WebKnowledgeSyncReport:  # type: ignore[no-untyped-def]
        self.attempts += 1
        if self.attempts == 1:
            raise RetryableWebFetchError(429, retry_after_seconds=60)
        return await super().validate(policy)


class PermanentMetadataFailureSynchronizer(SuccessfulSynchronizer):
    def __init__(self) -> None:
        self.attempts = 0

    async def validate(self, policy) -> WebKnowledgeSyncReport:  # type: ignore[no-untyped-def]
        del policy
        self.attempts += 1
        raise RuntimeError("knowledge_chunk_heading_too_long: heading length 1879 exceeds 500")


class ConcurrentSynchronizer(SuccessfulSynchronizer):
    def __init__(self, expected_concurrency: int) -> None:
        self.expected_concurrency = expected_concurrency
        self.active = 0
        self.maximum_active = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def validate(self, policy) -> WebKnowledgeSyncReport:  # type: ignore[no-untyped-def]
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        if self.active >= self.expected_concurrency:
            self.started.set()
        try:
            await self.release.wait()
            return await super().validate(policy)
        finally:
            self.active -= 1


class SuccessfulProductionSynchronizer:
    def __init__(self) -> None:
        self.begun: list[str] = []
        self.published: list[dict[str, object]] = []
        self.aborted: list[str] = []

    async def begin_staged_sync(self, policy, *, snapshot_id: str) -> None:  # type: ignore[no-untyped-def]
        del policy
        self.begun.append(snapshot_id)

    async def stage_page(self, policy, *, snapshot_id: str) -> WebKnowledgeSyncReport:  # type: ignore[no-untyped-def]
        url = policy.seed_urls[0]
        return WebKnowledgeSyncReport(
            sync_job_id=snapshot_id,
            discovered_count=1,
            document_count=1,
            indexed_count=1,
            skipped_count=0,
            excluded_count=0,
            failed_count=0,
            errors={},
            product_count=1,
            changed_document_count=1,
            validators=(
                WebCrawlValidator(
                    document_id=f"doc:{url}",
                    version_id=f"version:{url}",
                    canonical_url=url,
                    requested_url=url,
                    final_url=url,
                    product_key=url.rsplit("-", 1)[-1],
                ),
            ),
        )

    async def publish_staged_sync(self, policy, **values):  # type: ignore[no-untyped-def]
        del policy
        self.published.append(values)
        return ProductSnapshotActivation(
            snapshot_id=str(values["snapshot_id"]),
            activated_count=int(values["expected_product_count"]),
            pending_removal_count=0,
            expired_count=0,
        )

    async def abort_staged_sync(self, policy, *, snapshot_id: str, **_values) -> None:  # type: ignore[no-untyped-def]
        del policy
        self.aborted.append(snapshot_id)


class HangingFinalizationSynchronizer(SuccessfulProductionSynchronizer):
    def __init__(self) -> None:
        super().__init__()
        self.publication_canceled = asyncio.Event()

    async def publish_staged_sync(self, policy, **values):  # type: ignore[no-untyped-def]
        del policy, values
        try:
            await asyncio.Future()
        finally:
            self.publication_canceled.set()
        raise AssertionError("unreachable")


class NotModifiedWithoutStagedProductSynchronizer(SuccessfulProductionSynchronizer):
    async def stage_page(self, policy, *, snapshot_id: str) -> WebKnowledgeSyncReport:  # type: ignore[no-untyped-def]
        result = await super().stage_page(policy, snapshot_id=snapshot_id)
        if policy.seed_urls[0].endswith("product-1.html"):
            return replace(
                result,
                indexed_count=0,
                skipped_count=1,
                product_count=0,
                changed_document_count=0,
                http_not_modified_count=1,
            )
        return result


class DuplicateProductSynchronizer(SuccessfulProductionSynchronizer):
    async def stage_page(self, policy, *, snapshot_id: str) -> WebKnowledgeSyncReport:  # type: ignore[no-untyped-def]
        result = await super().stage_page(policy, snapshot_id=snapshot_id)
        validator = result.validators[0]
        return replace(
            result,
            validators=(replace(validator, product_key="duplicate-sku"),),
        )


class ExcludingProductionSynchronizer(SuccessfulProductionSynchronizer):
    def __init__(self, excluded_url: str, reason: str = "noindex") -> None:
        super().__init__()
        self.excluded_url = excluded_url
        self.reason = reason

    async def stage_page(self, policy, *, snapshot_id: str) -> WebKnowledgeSyncReport:  # type: ignore[no-untyped-def]
        url = policy.seed_urls[0]
        if url == self.excluded_url:
            return WebKnowledgeSyncReport(
                sync_job_id=snapshot_id,
                discovered_count=1,
                document_count=0,
                indexed_count=0,
                skipped_count=0,
                excluded_count=1,
                failed_count=0,
                errors={url: f"excluded: {self.reason}"},
            )
        return await super().stage_page(policy, snapshot_id=snapshot_id)


class RecoverableFinalizationSynchronizer(SuccessfulProductionSynchronizer):
    def __init__(self) -> None:
        super().__init__()
        self.fail_publication = True
        self.staged_urls: list[str] = []

    async def stage_page(self, policy, *, snapshot_id: str) -> WebKnowledgeSyncReport:  # type: ignore[no-untyped-def]
        self.staged_urls.append(policy.seed_urls[0])
        return await super().stage_page(policy, snapshot_id=snapshot_id)

    async def publish_staged_sync(self, policy, **values):  # type: ignore[no-untyped-def]
        if self.fail_publication:
            raise RuntimeError("temporary reconciliation defect")
        return await super().publish_staged_sync(policy, **values)


class RemediableProductionSynchronizer(SuccessfulProductionSynchronizer):
    def __init__(self, failing_url: str) -> None:
        super().__init__()
        self.failing_url = failing_url
        self.failure_enabled = True
        self.processed_urls: list[str] = []

    async def stage_page(self, policy, *, snapshot_id: str) -> WebKnowledgeSyncReport:  # type: ignore[no-untyped-def]
        url = policy.seed_urls[0]
        self.processed_urls.append(url)
        if self.failure_enabled and url == self.failing_url:
            return WebKnowledgeSyncReport(
                sync_job_id=snapshot_id,
                discovered_count=1,
                document_count=0,
                indexed_count=0,
                skipped_count=0,
                excluded_count=0,
                failed_count=1,
                errors={url: "temporary parser failure"},
            )
        return await super().stage_page(policy, snapshot_id=snapshot_id)


def _principal(*, can_sync: bool = True) -> AuthenticatedPrincipal:
    scopes = {"knowledge:read"}
    if can_sync:
        scopes.add("knowledge:sync")
    return AuthenticatedPrincipal(
        subject_id="admin-1",
        tenant_id="tenant-a",
        roles=frozenset({"knowledge_admin"}),
        scopes=frozenset(scopes),
        authentication_method="session",
        authenticated_at=datetime.now(UTC),
        correlation_id="correlation-1",
    )


def _command(principal: AuthenticatedPrincipal, key: str) -> EnqueueWebSyncJobCommand:
    return EnqueueWebSyncJobCommand(
        principal=principal,
        site_id="site-a",
        base_url="https://shop.example.com",
        primary_language="en",
        policy=WebSyncPolicySnapshot(max_pages=500),
        manifest_id="manifest-1",
        mode=WebSyncMode.SHADOW,
        sample_size=20,
        idempotency_key=key,
        correlation_id="correlation-1",
    )


def _manifest() -> WebCrawlManifest:
    return WebCrawlManifest(
        tenant_id="tenant-a",
        site_id="site-a",
        manifest_id="manifest-1",
        base_url="https://shop.example.com",
        root_sitemap_url="https://shop.example.com/sitemap.xml",
        primary_language="en",
        translation_provider="gtranslate",
        status=WebCrawlManifestStatus.READY,
        fingerprint="a" * 64,
        primary_sitemap_urls=("https://shop.example.com/sitemap-products.xml",),
        translated_locales=("de",),
        excluded_sitemap_count=1,
        excluded_url_count=0,
        blocking_reasons=(),
        created_by="admin-1",
        created_at=datetime.now(UTC),
        items=tuple(
            WebCrawlManifestItem(
                url=f"https://shop.example.com/product-{index}.html",
                source_sitemap_url="https://shop.example.com/sitemap-products.xml",
            )
            for index in range(500)
        ),
    )


async def test_service_deduplicates_active_site_jobs() -> None:
    store = InMemoryJobStore()
    manifest_store = InMemoryWebCrawlManifestStore((_manifest(),))
    service = WebSyncJobService(store, manifest_store)

    first = await service.enqueue(_command(_principal(), "request-1"))
    second = await service.enqueue(_command(_principal(), "request-2"))

    assert first.created is True
    assert second.created is False
    assert second.job.job_id == first.job.job_id
    listed = await service.list(ListWebSyncJobsQuery(_principal(), site_id="site-a"))
    assert listed.items == (first.job,)


async def test_service_requires_sync_permission() -> None:
    service = WebSyncJobService(InMemoryJobStore(), InMemoryWebCrawlManifestStore((_manifest(),)))

    with pytest.raises(PermissionError, match="knowledge:sync"):
        await service.enqueue(_command(_principal(can_sync=False), "request-1"))


async def test_service_rejects_expired_manifest() -> None:
    expired = replace(_manifest(), created_at=datetime.now(UTC) - timedelta(hours=2))
    service = WebSyncJobService(
        InMemoryJobStore(),
        InMemoryWebCrawlManifestStore((expired,)),
        manifest_max_age_hours=1,
    )

    with pytest.raises(ValueError, match="expired"):
        await service.enqueue(_command(_principal(), "request-1"))


async def test_service_rejects_manifest_from_an_outdated_source_config() -> None:
    manifest = replace(_manifest(), source_config_version=1)
    service = WebSyncJobService(
        InMemoryJobStore(source_config_version=2),
        InMemoryWebCrawlManifestStore((manifest,)),
    )

    with pytest.raises(ValueError, match="configuration changed"):
        await service.enqueue(_command(_principal(), "request-1"))


async def test_environment_flag_cannot_bypass_production_pipeline_gate() -> None:
    service = WebSyncJobService(
        InMemoryJobStore(),
        InMemoryWebCrawlManifestStore((_manifest(),)),
        production_enabled=True,
    )
    command = replace(
        _command(_principal(), "production-request"),
        mode=WebSyncMode.PRODUCTION,
        sample_size=None,
    )

    with pytest.raises(ValueError, match="staged publication"):
        await service.enqueue(command)


async def test_service_rejects_scope_above_manifest_safety_ceiling() -> None:
    manifest = replace(_manifest(), items=_manifest().items[:21])
    service = WebSyncJobService(
        InMemoryJobStore(),
        InMemoryWebCrawlManifestStore((manifest,)),
        production_enabled=True,
        production_pipeline_ready=True,
        manifest_safety_ceiling=20,
    )
    command = replace(
        _command(_principal(), "bounded-request"),
        policy=WebSyncPolicySnapshot(max_pages=20),
        mode=WebSyncMode.PRODUCTION,
        sample_size=None,
    )

    with pytest.raises(ValueError, match=r"manifest safety ceiling \(21 > 20\)"):
        await service.enqueue(command)


async def test_service_uses_high_safety_ceiling_instead_of_business_page_limit() -> None:
    manifest = replace(_manifest(), items=_manifest().items[:20])
    store = InMemoryJobStore()
    service = WebSyncJobService(
        store,
        InMemoryWebCrawlManifestStore((manifest,)),
    )
    command = replace(
        _command(_principal(), "bounded-request"),
        policy=WebSyncPolicySnapshot(max_pages=25),
    )

    result = await service.enqueue(command)

    assert result.job.expected_count == 20
    assert result.job.policy.max_pages == 50_000


async def test_service_supports_full_shadow_without_copying_urls_into_job_payload() -> None:
    store = InMemoryJobStore()
    service = WebSyncJobService(
        store,
        InMemoryWebCrawlManifestStore((_manifest(),)),
        full_shadow_enabled=True,
    )

    result = await service.enqueue(replace(_command(_principal(), "full-shadow"), sample_size=None))

    assert result.job.status is WebSyncJobStatus.PREPARING
    assert result.job.phase is WebSyncJobPhase.PREPARING
    assert result.job.expected_count == 500
    assert result.job.sample_size is None
    assert result.job.manifest_version == 1
    assert result.job.policy.seed_urls == ()
    assert result.job.policy.validators == ()
    assert store.items[result.job.job_id] == []


async def test_service_can_disable_full_shadow_independently() -> None:
    service = WebSyncJobService(
        InMemoryJobStore(),
        InMemoryWebCrawlManifestStore((_manifest(),)),
        full_shadow_enabled=False,
    )

    with pytest.raises(ValueError, match="full shadow synchronization is disabled"):
        await service.enqueue(
            replace(_command(_principal(), "full-shadow-disabled"), sample_size=None)
        )


async def test_worker_prepares_manifest_in_recoverable_batches() -> None:
    manifest = replace(_manifest(), items=_manifest().items[:20])
    store = InMemoryJobStore()
    manifest_store = InMemoryWebCrawlManifestStore((manifest,))
    service = WebSyncJobService(store, manifest_store)
    queued = await service.enqueue(_command(_principal(), "batched-preparation"))
    worker = WebSyncJobWorker(
        store=store,
        manifest_store=manifest_store,
        synchronizer=SuccessfulSynchronizer(),  # type: ignore[arg-type]
        prepare_batch_size=7,
        lease_seconds=60,
        heartbeat_seconds=10,
    )

    assert await worker.run_once(tenant_id="tenant-a", worker_id="worker-1") is True

    assert store.prepared_batches == [7, 7, 6]
    assert store.jobs[queued.job.job_id].prepared_count == 20
    assert store.jobs[queued.job.job_id].status is WebSyncJobStatus.SUCCEEDED


async def test_fresh_full_manifest_extracts_identities_from_first_prepared_item() -> None:
    manifest = replace(_manifest(), items=_manifest().items[:2])
    store = InMemoryJobStore()
    manifest_store = InMemoryWebCrawlManifestStore((manifest,))
    service = WebSyncJobService(store, manifest_store, full_shadow_enabled=True)
    queued = await service.enqueue(
        replace(_command(_principal(), "identity-cursor-zero"), sample_size=None)
    )
    synchronizer = IdentityTrackingSynchronizer()
    worker = WebSyncJobWorker(
        store=store,
        manifest_store=manifest_store,
        synchronizer=synchronizer,  # type: ignore[arg-type]
        lease_seconds=60,
        heartbeat_seconds=10,
    )

    assert await worker.run_once(tenant_id="tenant-a", worker_id="worker-1") is True

    expected_urls = [item.url for item in manifest.items]
    assert synchronizer.identity_urls == expected_urls
    assert [item.product_key for item in store.items[queued.job.job_id]] == [
        url.rsplit("/", 1)[-1] for url in expected_urls
    ]


async def test_shadow_sample_is_stable_and_distributed_across_manifest() -> None:
    manifest = _manifest()
    store = InMemoryJobStore()
    manifest_store = InMemoryWebCrawlManifestStore((manifest,))
    service = WebSyncJobService(store, manifest_store)
    queued = await service.enqueue(_command(_principal(), "stable-sample"))
    worker = WebSyncJobWorker(
        store=store,
        manifest_store=manifest_store,
        synchronizer=SuccessfulSynchronizer(),  # type: ignore[arg-type]
        lease_seconds=60,
        heartbeat_seconds=10,
    )

    assert await worker.run_once(tenant_id="tenant-a", worker_id="worker-1") is True

    expected = {
        item.url
        for item in sorted(
            manifest.items,
            key=lambda item: (
                md5(item.url.encode(), usedforsecurity=False).hexdigest(),
                item.url,
            ),
        )[:20]
    }
    prepared = {item.url for item in store.items[queued.job.job_id]}
    assert prepared == expected


async def test_worker_releases_delayed_retry_without_busy_reclaiming_job() -> None:
    manifest = replace(_manifest(), items=_manifest().items[:1])
    store = InMemoryJobStore()
    manifest_store = InMemoryWebCrawlManifestStore((manifest,))
    service = WebSyncJobService(store, manifest_store)
    queued = await service.enqueue(replace(_command(_principal(), "retry-after"), sample_size=None))
    synchronizer = RetryAfterSynchronizer()
    worker = WebSyncJobWorker(
        store=store,
        manifest_store=manifest_store,
        synchronizer=synchronizer,  # type: ignore[arg-type]
        lease_seconds=60,
        heartbeat_seconds=10,
    )

    assert await worker.run_once(tenant_id="tenant-a", worker_id="worker-1") is True

    delayed = store.items[queued.job.job_id][0]
    assert delayed.status is WebSyncJobItemStatus.PENDING
    assert delayed.next_attempt_at is not None
    assert delayed.next_attempt_at >= datetime.now(UTC) + timedelta(seconds=55)
    assert store.jobs[queued.job.job_id].status is WebSyncJobStatus.QUEUED
    assert await worker.run_once(tenant_id="tenant-a", worker_id="worker-2") is False
    assert synchronizer.attempts == 1

    store.items[queued.job.job_id][0] = replace(
        delayed,
        next_attempt_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    assert await worker.run_once(tenant_id="tenant-a", worker_id="worker-3") is True
    assert store.jobs[queued.job.job_id].status is WebSyncJobStatus.SUCCEEDED
    assert synchronizer.attempts == 2


async def test_worker_terminates_metadata_contract_failure_after_first_attempt() -> None:
    manifest = replace(_manifest(), items=_manifest().items[:1])
    store = InMemoryJobStore()
    manifest_store = InMemoryWebCrawlManifestStore((manifest,))
    service = WebSyncJobService(store, manifest_store)
    queued = await service.enqueue(
        replace(_command(_principal(), "metadata-overflow"), sample_size=None)
    )
    synchronizer = PermanentMetadataFailureSynchronizer()
    worker = WebSyncJobWorker(
        store=store,
        manifest_store=manifest_store,
        synchronizer=synchronizer,  # type: ignore[arg-type]
        lease_seconds=60,
        heartbeat_seconds=10,
    )

    assert await worker.run_once(tenant_id="tenant-a", worker_id="worker-1") is True

    failed = store.items[queued.job.job_id][0]
    assert synchronizer.attempts == 1
    assert failed.attempt_count == 1
    assert failed.status is WebSyncJobItemStatus.FAILED
    assert failed.error_code == "knowledge_chunk_heading_too_long"
    assert failed.next_attempt_at is None


async def test_worker_persists_full_incremental_report() -> None:
    store = InMemoryJobStore()
    manifest_store = InMemoryWebCrawlManifestStore((_manifest(),))
    service = WebSyncJobService(store, manifest_store)
    queued = await service.enqueue(_command(_principal(), "request-1"))
    worker = WebSyncJobWorker(
        store=store,
        manifest_store=manifest_store,
        synchronizer=SuccessfulSynchronizer(),  # type: ignore[arg-type]
        lease_seconds=60,
        heartbeat_seconds=10,
    )

    assert await worker.run_once(tenant_id="tenant-a", worker_id="worker-1") is True

    completed = store.jobs[queued.job.job_id]
    assert completed.status == WebSyncJobStatus.SUCCEEDED
    assert completed.report == WebSyncJobReport(
        pipeline_sync_job_id=completed.job_id,
        published=False,
        discovered_count=20,
        document_count=20,
        changed_document_count=0,
        unchanged_document_count=20,
        http_not_modified_count=20,
        duplicate_count=0,
        duplicate_product_count=0,
        product_count=20,
        processed_page_count=20,
        produced_document_count=20,
        pending_removal_count=0,
        expired_count=0,
        indexed_chunk_count=0,
        excluded_count=0,
        failed_count=0,
        errors={},
    )
    assert completed.publication_status is WebSyncPublicationStatus.NOT_REQUESTED


async def test_worker_processes_bounded_page_batch_concurrently() -> None:
    manifest = replace(_manifest(), items=_manifest().items[:20])
    store = InMemoryJobStore()
    manifest_store = InMemoryWebCrawlManifestStore((manifest,))
    service = WebSyncJobService(store, manifest_store)
    queued = await service.enqueue(_command(_principal(), "concurrent-request"))
    synchronizer = ConcurrentSynchronizer(expected_concurrency=4)
    worker = WebSyncJobWorker(
        store=store,
        manifest_store=manifest_store,
        synchronizer=synchronizer,  # type: ignore[arg-type]
        concurrency=4,
        lease_seconds=60,
        heartbeat_seconds=10,
    )

    execution = asyncio.create_task(worker.run_once(tenant_id="tenant-a", worker_id="worker-1"))
    await asyncio.wait_for(synchronizer.started.wait(), timeout=1)
    assert synchronizer.maximum_active == 4
    assert store.jobs[queued.job.job_id].completed_count == 0

    synchronizer.release.set()
    assert await execution is True
    assert store.jobs[queued.job.job_id].status is WebSyncJobStatus.SUCCEEDED


async def test_worker_stops_renewing_a_job_that_makes_no_progress() -> None:
    manifest = replace(_manifest(), items=_manifest().items[:1])
    store = InMemoryJobStore()
    manifest_store = InMemoryWebCrawlManifestStore((manifest,))
    service = WebSyncJobService(store, manifest_store)
    queued = await service.enqueue(replace(_command(_principal(), "no-progress"), sample_size=None))
    synchronizer = HangingSynchronizer()
    worker = WebSyncJobWorker(
        store=store,
        manifest_store=manifest_store,
        synchronizer=synchronizer,  # type: ignore[arg-type]
        concurrency=1,
        lease_seconds=1,
        heartbeat_seconds=0.01,  # type: ignore[arg-type]
        max_no_progress_seconds=0.03,
    )

    assert await asyncio.wait_for(
        worker.run_once(tenant_id="tenant-a", worker_id="worker-stalled"),
        timeout=0.5,
    )

    stalled = store.jobs[queued.job.job_id]
    assert synchronizer.canceled.is_set()
    assert stalled.status is WebSyncJobStatus.RUNNING
    assert stalled.lease_owner == "worker-stalled"


async def test_two_tenants_share_the_process_wide_page_limit() -> None:
    tenant_a_manifest = replace(_manifest(), items=_manifest().items[:20])
    tenant_b_manifest = replace(tenant_a_manifest, tenant_id="tenant-b")
    store = InMemoryJobStore()
    manifest_store = InMemoryWebCrawlManifestStore((tenant_a_manifest, tenant_b_manifest))
    service = WebSyncJobService(store, manifest_store)
    tenant_a = await service.enqueue(_command(_principal(), "tenant-a-concurrent"))
    tenant_b_principal = replace(_principal(), tenant_id="tenant-b")
    tenant_b = await service.enqueue(_command(tenant_b_principal, "tenant-b-concurrent"))
    synchronizer = ConcurrentSynchronizer(expected_concurrency=3)
    worker = WebSyncJobWorker(
        store=store,
        manifest_store=manifest_store,
        synchronizer=synchronizer,  # type: ignore[arg-type]
        concurrency=2,
        lease_seconds=60,
        heartbeat_seconds=10,
        execution_budget=WebSyncExecutionBudget(page_slots=3, finalization_slots=1),
    )

    executions = (
        asyncio.create_task(worker.run_once(tenant_id="tenant-a", worker_id="worker-a")),
        asyncio.create_task(worker.run_once(tenant_id="tenant-b", worker_id="worker-b")),
    )
    await asyncio.wait_for(synchronizer.started.wait(), timeout=1)
    assert synchronizer.maximum_active == 3

    synchronizer.release.set()
    assert await asyncio.gather(*executions) == [True, True]
    assert store.jobs[tenant_a.job.job_id].status is WebSyncJobStatus.SUCCEEDED
    assert store.jobs[tenant_b.job.job_id].status is WebSyncJobStatus.SUCCEEDED


async def test_quantum_completion_goes_to_finalization_without_requeue() -> None:
    manifest = replace(_manifest(), items=_manifest().items[:1])

    class StrictStore(InMemoryJobStore):
        requeue_calls = 0

        async def requeue_incomplete(self, **kwargs):  # type: ignore[no-untyped-def]
            self.requeue_calls += 1
            job = self.jobs[kwargs["job_id"]]
            if job.completed_count >= job.expected_count:
                raise AssertionError("completed job was requeued")
            return await super().requeue_incomplete(**kwargs)

    store = StrictStore()
    manifest_store = InMemoryWebCrawlManifestStore((manifest,))
    service = WebSyncJobService(store, manifest_store, full_shadow_enabled=True)
    queued = await service.enqueue(
        replace(_command(_principal(), "quantum-final-page"), sample_size=None)
    )
    worker = WebSyncJobWorker(
        store=store,
        manifest_store=manifest_store,
        synchronizer=SuccessfulSynchronizer(),  # type: ignore[arg-type]
        concurrency=1,
        lease_seconds=60,
        heartbeat_seconds=10,
    )

    assert (
        await worker.run_once(
            tenant_id="tenant-a",
            worker_id="worker-prep",
            max_pages=1,
        )
        is True
    )
    assert store.jobs[queued.job.job_id].status is WebSyncJobStatus.PREPARING
    assert (
        await worker.run_once(
            tenant_id="tenant-a",
            worker_id="worker-process",
            max_pages=1,
        )
        is True
    )
    assert store.jobs[queued.job.job_id].status is WebSyncJobStatus.QUEUED
    assert (
        await worker.run_once(
            tenant_id="tenant-a",
            worker_id="worker-finalize",
            max_pages=1,
        )
        is True
    )
    assert store.jobs[queued.job.job_id].status is WebSyncJobStatus.SUCCEEDED
    assert store.requeue_calls == 0


async def test_worker_runs_reconciled_production_publication() -> None:
    manifest = replace(_manifest(), items=_manifest().items[:2])
    store = InMemoryJobStore()
    manifest_store = InMemoryWebCrawlManifestStore((manifest,))
    service = WebSyncJobService(
        store,
        manifest_store,
        production_enabled=True,
        production_pipeline_ready=True,
    )
    command = replace(
        _command(_principal(), "production-request"),
        mode=WebSyncMode.PRODUCTION,
        sample_size=None,
    )
    queued = await service.enqueue(command)
    synchronizer = SuccessfulProductionSynchronizer()
    worker = WebSyncJobWorker(
        store=store,
        manifest_store=manifest_store,
        synchronizer=synchronizer,  # type: ignore[arg-type]
        lease_seconds=60,
        heartbeat_seconds=10,
    )

    assert await worker.run_once(tenant_id="tenant-a", worker_id="worker-1") is True

    completed = store.jobs[queued.job.job_id]
    assert completed.status is WebSyncJobStatus.SUCCEEDED
    assert completed.publication_status is WebSyncPublicationStatus.PUBLISHED
    assert completed.report is not None and completed.report.published is True
    assert completed.report.product_count == 2
    assert synchronizer.begun == [queued.job.job_id]
    assert synchronizer.published[0]["expected_chunk_count"] == 2
    assert synchronizer.published[0]["expected_product_count"] == 2
    assert synchronizer.aborted == []
    assert len(manifest_store.page_states) == 2
    frozen_manifest = await manifest_store.get(
        tenant_id="tenant-a",
        site_id="site-a",
        manifest_id=manifest.manifest_id,
    )
    assert frozen_manifest is not None
    assert all(item.document_id is None for item in frozen_manifest.items)


async def test_production_blocks_unresolved_duplicate_product_identities() -> None:
    manifest = replace(_manifest(), items=_manifest().items[:2])
    store = InMemoryJobStore()
    manifest_store = InMemoryWebCrawlManifestStore((manifest,))
    service = WebSyncJobService(
        store,
        manifest_store,
        production_enabled=True,
        production_pipeline_ready=True,
    )
    queued = await service.enqueue(
        replace(
            _command(_principal(), "duplicate-product-identities"),
            mode=WebSyncMode.PRODUCTION,
            sample_size=None,
        )
    )
    synchronizer = DuplicateProductSynchronizer()
    worker = WebSyncJobWorker(
        store=store,
        manifest_store=manifest_store,
        synchronizer=synchronizer,  # type: ignore[arg-type]
        lease_seconds=60,
        heartbeat_seconds=10,
    )

    assert await worker.run_once(tenant_id="tenant-a", worker_id="worker-1") is True

    blocked = store.jobs[queued.job.job_id]
    assert blocked.status is WebSyncJobStatus.BLOCKED
    assert blocked.report is not None
    assert blocked.report.duplicate_product_count == 1
    assert blocked.report.failed_count == 0
    assert blocked.report.processed_page_count == 2
    assert blocked.report.produced_document_count == 2
    assert blocked.report.failed_page_count == 0
    assert blocked.report.unresolved_product_identity_count == 1
    assert blocked.report.blocking_issue_count == 1
    assert blocked.report.publication_block_reasons == ("unresolved_product_identity",)
    assert "product_identity_gate" in blocked.report.errors
    assert synchronizer.published == []


async def test_finalization_timeout_is_quarantined_without_aborting_staging() -> None:
    manifest = replace(_manifest(), items=_manifest().items[:1])
    store = InMemoryJobStore()
    manifest_store = InMemoryWebCrawlManifestStore((manifest,))
    service = WebSyncJobService(
        store,
        manifest_store,
        production_enabled=True,
        production_pipeline_ready=True,
    )
    queued = await service.enqueue(
        replace(
            _command(_principal(), "finalization-timeout"),
            mode=WebSyncMode.PRODUCTION,
            sample_size=None,
        )
    )
    synchronizer = HangingFinalizationSynchronizer()
    worker = WebSyncJobWorker(
        store=store,
        manifest_store=manifest_store,
        synchronizer=synchronizer,  # type: ignore[arg-type]
        lease_seconds=1,
        heartbeat_seconds=0.01,  # type: ignore[arg-type]
        max_no_progress_seconds=0.2,
        finalization_timeout_seconds=0.03,
    )

    assert await asyncio.wait_for(
        worker.run_once(tenant_id="tenant-a", worker_id="worker-finalizer"),
        timeout=0.5,
    )

    blocked = store.jobs[queued.job.job_id]
    assert synchronizer.publication_canceled.is_set()
    assert synchronizer.aborted == []
    assert blocked.status is WebSyncJobStatus.BLOCKED
    assert blocked.report is not None
    assert blocked.report.errors["finalization"] == "website sync finalization timed out"


async def test_stale_version_reference_is_reported_as_publication_block() -> None:
    class StaleVersionSynchronizer(SuccessfulProductionSynchronizer):
        async def publish_staged_sync(self, policy, **values):  # type: ignore[no-untyped-def]
            del policy, values
            raise RuntimeError("stale_version_reference: version_id=old:stale_or_unpublishable")

    manifest = replace(_manifest(), items=_manifest().items[:1])
    store = InMemoryJobStore()
    manifest_store = InMemoryWebCrawlManifestStore((manifest,))
    service = WebSyncJobService(
        store,
        manifest_store,
        production_enabled=True,
        production_pipeline_ready=True,
    )
    queued = await service.enqueue(
        replace(
            _command(_principal(), "stale-version-publication"),
            mode=WebSyncMode.PRODUCTION,
            sample_size=None,
        )
    )
    worker = WebSyncJobWorker(
        store=store,
        manifest_store=manifest_store,
        synchronizer=StaleVersionSynchronizer(),  # type: ignore[arg-type]
        lease_seconds=60,
        heartbeat_seconds=10,
    )

    assert await worker.run_once(tenant_id="tenant-a", worker_id="worker-stale") is True

    blocked = store.jobs[queued.job.job_id]
    assert blocked.status is WebSyncJobStatus.BLOCKED
    assert blocked.report is not None
    assert blocked.report.publication_block_reasons == ("stale_version_reference",)
    assert blocked.report.errors["finalization"].startswith("stale_version_reference:")


async def test_stale_version_reconciliation_requeues_only_invalid_references() -> None:
    class StaleVersionSynchronizer(SuccessfulProductionSynchronizer):
        async def publish_staged_sync(self, policy, **values):  # type: ignore[no-untyped-def]
            del policy, values
            raise RuntimeError("stale_version_reference: version_id=old:stale_or_unpublishable")

    class InvalidVersionControlPlane:
        async def invalid_site_publication_version_ids(self, **values):  # type: ignore[no-untyped-def]
            assert values["version_ids"] == ("version:https://shop.example.com/product-0.html",)
            return values["version_ids"]

    manifest = replace(_manifest(), items=_manifest().items[:1])
    store = InMemoryJobStore()
    manifest_store = InMemoryWebCrawlManifestStore((manifest,))
    service = WebSyncJobService(
        store,
        manifest_store,
        production_enabled=True,
        production_pipeline_ready=True,
        knowledge_control_plane=InvalidVersionControlPlane(),  # type: ignore[arg-type]
    )
    queued = await service.enqueue(
        replace(
            _command(_principal(), "reconcile-stale-version"),
            mode=WebSyncMode.PRODUCTION,
            sample_size=None,
        )
    )
    worker = WebSyncJobWorker(
        store=store,
        manifest_store=manifest_store,
        synchronizer=StaleVersionSynchronizer(),  # type: ignore[arg-type]
        lease_seconds=60,
        heartbeat_seconds=10,
    )
    assert await worker.run_once(tenant_id="tenant-a", worker_id="worker-stale") is True

    reconciled = await service.reconcile_stale_versions(
        ReconcileStaleWebSyncJobCommand(_principal(), queued.job.job_id)
    )

    assert reconciled.status is WebSyncJobStatus.QUEUED
    assert reconciled.phase is WebSyncJobPhase.QUEUED
    assert reconciled.completed_count == 0
    item = store.items[queued.job.job_id][0]
    assert item.status is WebSyncJobItemStatus.PENDING
    assert item.version_id is None
    assert item.etag is None
    assert item.last_modified is None


async def test_cleanup_pending_production_job_aborts_staging_before_terminal_failure() -> None:
    class CleanupStore(InMemoryJobStore):
        async def claim_next(self, **kwargs):  # type: ignore[no-untyped-def]
            for job_id, job in self.jobs.items():
                if job.status is not WebSyncJobStatus.CLEANUP_PENDING:
                    continue
                claimed = replace(
                    job,
                    lease_owner=kwargs["worker_id"],
                    lease_expires_at=kwargs["lease_expires_at"],
                )
                self.jobs[job_id] = claimed
                return claimed
            return await super().claim_next(**kwargs)

    manifest = replace(_manifest(), items=_manifest().items[:1])
    store = CleanupStore()
    manifest_store = InMemoryWebCrawlManifestStore((manifest,))
    service = WebSyncJobService(
        store,
        manifest_store,
        production_enabled=True,
        production_pipeline_ready=True,
    )
    queued = await service.enqueue(
        replace(
            _command(_principal(), "cleanup-pending"),
            mode=WebSyncMode.PRODUCTION,
            sample_size=None,
        )
    )
    store.jobs[queued.job.job_id] = replace(
        queued.job,
        status=WebSyncJobStatus.CLEANUP_PENDING,
        phase=WebSyncJobPhase.AWAITING_REMEDIATION,
        available_at=datetime.now(UTC),
    )
    synchronizer = SuccessfulProductionSynchronizer()
    worker = WebSyncJobWorker(
        store=store,
        manifest_store=manifest_store,
        synchronizer=synchronizer,  # type: ignore[arg-type]
        lease_seconds=1,
        heartbeat_seconds=0.01,  # type: ignore[arg-type]
        max_no_progress_seconds=0.2,
        finalization_timeout_seconds=0.05,
    )

    assert await worker.run_once(tenant_id="tenant-a", worker_id="worker-cleanup") is True

    assert synchronizer.aborted == [queued.job.job_id]
    assert store.jobs[queued.job.job_id].status is WebSyncJobStatus.FAILED
    assert store.jobs[queued.job.job_id].error_code == "staging_cleanup_completed"


async def test_production_reconciliation_ignores_unstaged_304_product_key() -> None:
    manifest = replace(_manifest(), items=_manifest().items[:2])
    store = InMemoryJobStore()
    manifest_store = InMemoryWebCrawlManifestStore((manifest,))
    service = WebSyncJobService(
        store,
        manifest_store,
        production_enabled=True,
        production_pipeline_ready=True,
    )
    queued = await service.enqueue(
        replace(
            _command(_principal(), "production-not-modified-without-active-product"),
            mode=WebSyncMode.PRODUCTION,
            sample_size=None,
        )
    )
    synchronizer = NotModifiedWithoutStagedProductSynchronizer()
    worker = WebSyncJobWorker(
        store=store,
        manifest_store=manifest_store,
        synchronizer=synchronizer,  # type: ignore[arg-type]
        lease_seconds=60,
        heartbeat_seconds=10,
    )

    assert await worker.run_once(tenant_id="tenant-a", worker_id="worker-1") is True

    completed = store.jobs[queued.job.job_id]
    assert completed.status is WebSyncJobStatus.SUCCEEDED
    assert completed.report is not None
    assert completed.report.product_count == 1
    assert synchronizer.published[0]["expected_product_count"] == 1


@pytest.mark.parametrize("exclusion_reason", ["noindex", "unusable_content:empty"])
async def test_production_exclusion_does_not_republish_stale_content(
    exclusion_reason: str,
) -> None:
    source = _manifest()
    items = tuple(
        replace(
            item,
            document_id=f"old-document-{index}",
            version_id=f"old-version-{index}",
            canonical_url=item.url,
            final_url=item.url,
            artifact_status="published",
            content_kind="guide" if index == 1 else item.content_kind,
        )
        for index, item in enumerate(source.items[:2])
    )
    manifest = replace(source, items=items)
    excluded_url = items[1].url
    store = InMemoryJobStore()
    manifest_store = InMemoryWebCrawlManifestStore((manifest,))
    service = WebSyncJobService(
        store,
        manifest_store,
        production_enabled=True,
        production_pipeline_ready=True,
    )
    queued = await service.enqueue(
        replace(
            _command(_principal(), "production-exclusion"),
            mode=WebSyncMode.PRODUCTION,
            sample_size=None,
        )
    )
    synchronizer = ExcludingProductionSynchronizer(excluded_url, exclusion_reason)
    worker = WebSyncJobWorker(
        store=store,
        manifest_store=manifest_store,
        synchronizer=synchronizer,  # type: ignore[arg-type]
        lease_seconds=60,
        heartbeat_seconds=10,
    )

    assert await worker.run_once(tenant_id="tenant-a", worker_id="worker-1") is True

    completed = store.jobs[queued.job.job_id]
    assert completed.status is WebSyncJobStatus.SUCCEEDED
    assert synchronizer.published[0]["active_version_ids"] == (f"version:{items[0].url}",)
    assert ("tenant-a", "site-a", excluded_url) not in manifest_store.page_states


async def test_finalization_failure_retries_without_reprocessing_pages() -> None:
    manifest = replace(_manifest(), items=_manifest().items[:2])
    store = InMemoryJobStore()
    manifest_store = InMemoryWebCrawlManifestStore((manifest,))
    service = WebSyncJobService(
        store,
        manifest_store,
        production_enabled=True,
        production_pipeline_ready=True,
    )
    queued = await service.enqueue(
        replace(
            _command(_principal(), "recover-finalization"),
            mode=WebSyncMode.PRODUCTION,
            sample_size=None,
        )
    )
    synchronizer = RecoverableFinalizationSynchronizer()
    worker = WebSyncJobWorker(
        store=store,
        manifest_store=manifest_store,
        synchronizer=synchronizer,  # type: ignore[arg-type]
        lease_seconds=60,
        heartbeat_seconds=10,
    )

    assert await worker.run_once(tenant_id="tenant-a", worker_id="worker-1") is True
    blocked = store.jobs[queued.job.job_id]
    assert blocked.status is WebSyncJobStatus.BLOCKED
    assert blocked.report is not None
    assert blocked.report.errors["finalization"] == "temporary reconciliation defect"
    staged_before_retry = tuple(synchronizer.staged_urls)

    await service.retry(RetryWebSyncJobCommand(_principal(), queued.job.job_id))
    synchronizer.fail_publication = False
    assert await worker.run_once(tenant_id="tenant-a", worker_id="worker-2") is True

    completed = store.jobs[queued.job.job_id]
    assert completed.status is WebSyncJobStatus.SUCCEEDED
    assert tuple(synchronizer.staged_urls) == staged_before_retry
    assert len(synchronizer.published) == 1


async def test_blocked_production_job_retries_only_failed_page() -> None:
    manifest = replace(_manifest(), items=_manifest().items[:2])
    failing_url = manifest.items[1].url
    store = InMemoryJobStore()
    manifest_store = InMemoryWebCrawlManifestStore((manifest,))
    service = WebSyncJobService(
        store,
        manifest_store,
        production_enabled=True,
        production_pipeline_ready=True,
    )
    queued = await service.enqueue(
        replace(
            _command(_principal(), "remediable-production"),
            mode=WebSyncMode.PRODUCTION,
            sample_size=None,
        )
    )
    synchronizer = RemediableProductionSynchronizer(failing_url)
    worker = WebSyncJobWorker(
        store=store,
        manifest_store=manifest_store,
        synchronizer=synchronizer,  # type: ignore[arg-type]
        lease_seconds=60,
        heartbeat_seconds=10,
    )

    assert await worker.run_once(tenant_id="tenant-a", worker_id="worker-1") is True

    blocked = store.jobs[queued.job.job_id]
    assert blocked.status is WebSyncJobStatus.BLOCKED
    assert blocked.phase is WebSyncJobPhase.AWAITING_REMEDIATION
    assert blocked.failed_item_count == 1
    assert blocked.retention_expires_at is not None
    assert synchronizer.processed_urls.count(manifest.items[0].url) == 1
    assert synchronizer.processed_urls.count(failing_url) == 1
    assert synchronizer.published == []
    assert synchronizer.aborted == []

    synchronizer.failure_enabled = False
    retried = await service.retry(
        RetryWebSyncJobCommand(principal=_principal(), job_id=queued.job.job_id)
    )
    assert retried.status is WebSyncJobStatus.QUEUED
    assert retried.completed_count == 1

    assert await worker.run_once(tenant_id="tenant-a", worker_id="worker-2") is True

    completed = store.jobs[queued.job.job_id]
    assert completed.status is WebSyncJobStatus.SUCCEEDED
    assert completed.publication_status is WebSyncPublicationStatus.PUBLISHED
    assert synchronizer.processed_urls.count(manifest.items[0].url) == 1
    assert synchronizer.processed_urls.count(failing_url) == 2
    assert synchronizer.begun == [queued.job.job_id, queued.job.job_id]
    assert synchronizer.aborted == []


async def test_canceling_blocked_production_job_discards_staged_snapshot() -> None:
    manifest = replace(_manifest(), items=_manifest().items[:1])
    failing_url = manifest.items[0].url
    store = InMemoryJobStore()
    manifest_store = InMemoryWebCrawlManifestStore((manifest,))
    service = WebSyncJobService(
        store,
        manifest_store,
        production_enabled=True,
        production_pipeline_ready=True,
    )
    queued = await service.enqueue(
        replace(
            _command(_principal(), "abandoned-production"),
            mode=WebSyncMode.PRODUCTION,
            sample_size=None,
        )
    )
    synchronizer = RemediableProductionSynchronizer(failing_url)
    worker = WebSyncJobWorker(
        store=store,
        manifest_store=manifest_store,
        synchronizer=synchronizer,  # type: ignore[arg-type]
        lease_seconds=60,
        heartbeat_seconds=10,
    )
    assert await worker.run_once(tenant_id="tenant-a", worker_id="worker-1") is True
    assert store.jobs[queued.job.job_id].status is WebSyncJobStatus.BLOCKED

    await store.request_cancel(
        tenant_id="tenant-a",
        job_id=queued.job.job_id,
        requested_by="admin-1",
        requested_at=datetime.now(UTC),
    )
    assert await worker.run_once(tenant_id="tenant-a", worker_id="worker-2") is True

    canceled = store.jobs[queued.job.job_id]
    assert canceled.status is WebSyncJobStatus.CANCELED
    assert synchronizer.aborted == [queued.job.job_id]
