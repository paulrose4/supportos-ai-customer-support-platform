from dataclasses import dataclass

from app.domain.models import AuthenticatedPrincipal
from app.domain.models.web_sync_job import (
    WebSyncJob,
    WebSyncJobItem,
    WebSyncJobItemStatus,
    WebSyncMode,
    WebSyncPolicySnapshot,
    WebSyncTrigger,
)


@dataclass(frozen=True, slots=True)
class EnqueueWebSyncJobCommand:
    principal: AuthenticatedPrincipal
    site_id: str
    base_url: str
    primary_language: str
    policy: WebSyncPolicySnapshot
    manifest_id: str
    mode: WebSyncMode
    sample_size: int | None
    idempotency_key: str
    correlation_id: str
    trigger: WebSyncTrigger = WebSyncTrigger.MANUAL


@dataclass(frozen=True, slots=True)
class ListWebSyncJobsQuery:
    principal: AuthenticatedPrincipal
    site_id: str | None = None
    limit: int = 20


@dataclass(frozen=True, slots=True)
class GetWebSyncJobQuery:
    principal: AuthenticatedPrincipal
    job_id: str


@dataclass(frozen=True, slots=True)
class CancelWebSyncJobCommand:
    principal: AuthenticatedPrincipal
    job_id: str


@dataclass(frozen=True, slots=True)
class RetryWebSyncJobCommand:
    principal: AuthenticatedPrincipal
    job_id: str


@dataclass(frozen=True, slots=True)
class ReconcileStaleWebSyncJobCommand:
    principal: AuthenticatedPrincipal
    job_id: str


@dataclass(frozen=True, slots=True)
class ListWebSyncJobItemsQuery:
    principal: AuthenticatedPrincipal
    job_id: str
    status: WebSyncJobItemStatus | None = None
    limit: int = 100
    offset: int = 0


@dataclass(frozen=True, slots=True)
class EnqueueWebSyncJobResult:
    job: WebSyncJob
    created: bool


@dataclass(frozen=True, slots=True)
class ListWebSyncJobsResult:
    items: tuple[WebSyncJob, ...]


@dataclass(frozen=True, slots=True)
class ListWebSyncJobItemsResult:
    items: tuple[WebSyncJobItem, ...]
