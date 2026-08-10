from datetime import datetime
from typing import Protocol

from app.domain.models.web_sync_job import (
    WebSyncJob,
    WebSyncJobItem,
    WebSyncJobItemStatus,
    WebSyncJobReport,
    WebSyncPolicySnapshot,
)


class WebSyncSourceConfigVersionConflictError(RuntimeError):
    pass


class WebSyncJobStorePort(Protocol):
    async def enqueue(
        self,
        job: WebSyncJob,
        items: tuple[WebSyncJobItem, ...] = (),
        *,
        expected_source_config_version: int = 0,
    ) -> tuple[WebSyncJob, bool]: ...

    async def append_prepared_items(
        self,
        *,
        tenant_id: str,
        job_id: str,
        worker_id: str,
        items: tuple[WebSyncJobItem, ...],
    ) -> WebSyncJob: ...

    async def finish_preparation(
        self,
        *,
        tenant_id: str,
        job_id: str,
        worker_id: str,
        manifest_fingerprint: str,
    ) -> WebSyncJob: ...

    async def yield_preparation(
        self,
        *,
        tenant_id: str,
        job_id: str,
        worker_id: str,
        stage: str,
        cursor: int,
        yielded_at: datetime,
    ) -> WebSyncJob: ...

    async def apply_duplicate_policy(
        self,
        *,
        tenant_id: str,
        job_id: str,
        worker_id: str,
        policy: WebSyncPolicySnapshot,
    ) -> int: ...

    async def apply_duplicate_policy_batch(
        self,
        *,
        tenant_id: str,
        job_id: str,
        worker_id: str,
        policy: WebSyncPolicySnapshot,
        cursor: int,
        limit: int,
    ) -> tuple[int, int, bool]: ...

    async def promote_duplicate_fallbacks(
        self,
        *,
        tenant_id: str,
        job_id: str,
        worker_id: str,
    ) -> int: ...

    async def update_item_identity(
        self,
        *,
        tenant_id: str,
        job_id: str,
        item_id: str,
        worker_id: str,
        validator: WebSyncJobItem,
    ) -> None: ...

    async def requeue_incomplete(
        self,
        *,
        tenant_id: str,
        job_id: str,
        worker_id: str,
        requeued_at: datetime,
    ) -> WebSyncJob: ...

    async def get(self, *, tenant_id: str, job_id: str) -> WebSyncJob | None: ...

    async def list_recent(
        self,
        *,
        tenant_id: str,
        site_id: str | None,
        limit: int,
    ) -> tuple[WebSyncJob, ...]: ...

    async def claim_next(
        self,
        *,
        tenant_id: str,
        worker_id: str,
        claimed_at: datetime,
        lease_expires_at: datetime,
    ) -> WebSyncJob | None: ...

    async def heartbeat(
        self,
        *,
        tenant_id: str,
        job_id: str,
        worker_id: str,
        heartbeat_at: datetime,
        lease_expires_at: datetime,
    ) -> bool: ...

    async def request_cancel(
        self,
        *,
        tenant_id: str,
        job_id: str,
        requested_by: str,
        requested_at: datetime,
    ) -> WebSyncJob: ...

    async def cancel(
        self,
        *,
        tenant_id: str,
        job_id: str,
        worker_id: str,
        canceled_at: datetime,
    ) -> WebSyncJob: ...

    async def block(
        self,
        *,
        tenant_id: str,
        job_id: str,
        worker_id: str,
        report: WebSyncJobReport,
        blocked_at: datetime,
        retention_expires_at: datetime,
    ) -> WebSyncJob: ...

    async def retry_blocked(
        self,
        *,
        tenant_id: str,
        job_id: str,
        requested_by: str,
        requested_at: datetime,
    ) -> WebSyncJob: ...

    async def list_referenced_version_ids(
        self,
        *,
        tenant_id: str,
        job_id: str,
    ) -> tuple[str, ...]: ...

    async def reconcile_stale_versions(
        self,
        *,
        tenant_id: str,
        job_id: str,
        version_ids: tuple[str, ...],
        requested_by: str,
        requested_at: datetime,
    ) -> WebSyncJob: ...

    async def claim_next_item(
        self,
        *,
        tenant_id: str,
        job_id: str,
        worker_id: str,
        claimed_at: datetime,
        lease_expires_at: datetime,
    ) -> WebSyncJobItem | None: ...

    async def complete_item(
        self,
        *,
        tenant_id: str,
        job_id: str,
        item_id: str,
        worker_id: str,
        status: WebSyncJobItemStatus,
        report: WebSyncJobReport,
        validator: WebSyncJobItem | None,
        duration_ms: int,
        completed_at: datetime,
    ) -> WebSyncJobItem: ...

    async def fail_item(
        self,
        *,
        tenant_id: str,
        job_id: str,
        item_id: str,
        worker_id: str,
        error_code: str,
        error_message: str,
        duration_ms: int,
        failed_at: datetime,
        next_attempt_at: datetime | None = None,
        terminal: bool | None = None,
    ) -> WebSyncJobItem: ...

    async def list_items(
        self,
        *,
        tenant_id: str,
        job_id: str,
        status: WebSyncJobItemStatus | None,
        limit: int,
        offset: int = 0,
    ) -> tuple[WebSyncJobItem, ...]: ...

    async def aggregate_report(
        self,
        *,
        tenant_id: str,
        job_id: str,
    ) -> WebSyncJobReport: ...

    async def start_finalization(
        self,
        *,
        tenant_id: str,
        job_id: str,
        worker_id: str,
        started_at: datetime,
    ) -> WebSyncJob: ...

    async def complete(
        self,
        *,
        tenant_id: str,
        job_id: str,
        worker_id: str,
        report: WebSyncJobReport,
        completed_at: datetime,
    ) -> WebSyncJob: ...

    async def fail(
        self,
        *,
        tenant_id: str,
        job_id: str,
        worker_id: str,
        error_code: str,
        error_message: str,
        failed_at: datetime,
    ) -> WebSyncJob: ...

    async def release_cleanup(
        self,
        *,
        tenant_id: str,
        job_id: str,
        worker_id: str,
        available_at: datetime,
        error_message: str,
    ) -> WebSyncJob: ...
