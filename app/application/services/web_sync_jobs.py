from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.application.dto.web_sync_jobs import (
    CancelWebSyncJobCommand,
    EnqueueWebSyncJobCommand,
    EnqueueWebSyncJobResult,
    GetWebSyncJobQuery,
    ListWebSyncJobItemsQuery,
    ListWebSyncJobItemsResult,
    ListWebSyncJobsQuery,
    ListWebSyncJobsResult,
    ReconcileStaleWebSyncJobCommand,
    RetryWebSyncJobCommand,
)
from app.domain.models.web_crawl_manifest import WebCrawlManifestStatus
from app.domain.models.web_sync_job import (
    WebSyncJob,
    WebSyncJobPhase,
    WebSyncJobStatus,
    WebSyncMode,
    WebSyncPublicationStatus,
)
from app.domain.ports.knowledge import KnowledgeControlPlanePort
from app.domain.ports.web_crawl_manifests import WebCrawlManifestStorePort
from app.domain.ports.web_sync_jobs import (
    WebSyncJobStorePort,
    WebSyncSourceConfigVersionConflictError,
)
from app.domain.rules.duplicate_product import duplicate_product_policy_is_supported

_SHADOW_SAMPLE_SIZES = frozenset({20, 100, 200, 500})


class WebSyncJobService:
    def __init__(
        self,
        store: WebSyncJobStorePort,
        manifest_store: WebCrawlManifestStorePort,
        *,
        production_enabled: bool = False,
        production_pipeline_ready: bool = False,
        manifest_max_age_hours: int = 24,
        manifest_safety_ceiling: int = 50_000,
        full_shadow_enabled: bool = True,
        knowledge_control_plane: KnowledgeControlPlanePort | None = None,
    ) -> None:
        self._store = store
        self._manifest_store = manifest_store
        self._production_enabled = production_enabled
        self._production_pipeline_ready = production_pipeline_ready
        self._manifest_max_age = timedelta(hours=manifest_max_age_hours)
        self._manifest_safety_ceiling = manifest_safety_ceiling
        self._full_shadow_enabled = full_shadow_enabled
        self._knowledge_control_plane = knowledge_control_plane

    async def enqueue(self, command: EnqueueWebSyncJobCommand) -> EnqueueWebSyncJobResult:
        _require_scope(command.principal.scopes, "knowledge:sync")
        if not duplicate_product_policy_is_supported(command.policy.duplicate_product_policy):
            raise ValueError("unsupported duplicate product policy")
        if command.policy.duplicate_product_order != "manifest_ordinal":
            raise ValueError("unsupported duplicate product ordering")
        site_id = command.site_id.strip()
        base_url = command.base_url.strip()
        idempotency_key = command.idempotency_key.strip()
        manifest_id = command.manifest_id.strip()
        if not site_id or not base_url or not idempotency_key or not manifest_id:
            raise ValueError("site ID, base URL, manifest ID, and idempotency key are required")
        manifest = await self._manifest_store.get_metadata(
            tenant_id=command.principal.tenant_id,
            site_id=site_id,
            manifest_id=manifest_id,
        )
        if manifest is None:
            raise LookupError("website crawl manifest was not found")
        if manifest.status is not WebCrawlManifestStatus.READY:
            raise ValueError("website crawl manifest is blocked")
        if manifest.base_url.rstrip("/") != base_url.rstrip("/"):
            raise ValueError("website crawl manifest does not match the current site base URL")
        if (
            manifest.primary_language.replace("_", "-").casefold()
            != command.primary_language.replace("_", "-").casefold()
        ):
            raise ValueError("website crawl manifest does not match the current primary language")
        if datetime.now(UTC) - manifest.created_at > self._manifest_max_age:
            raise ValueError("website crawl manifest has expired; run preflight again")
        selected_count = _selected_count(
            manifest.url_count,
            command.mode,
            command.sample_size,
            full_shadow_enabled=self._full_shadow_enabled,
        )
        if selected_count > self._manifest_safety_ceiling:
            raise ValueError(
                "website synchronization scope exceeds the manifest safety ceiling "
                f"({selected_count} > {self._manifest_safety_ceiling}); "
                "capacity approval is required"
            )
        if command.mode is WebSyncMode.PRODUCTION and not self.production_available:
            raise ValueError(
                "production website synchronization is disabled until staged publication "
                "and PostgreSQL/Qdrant reconciliation are enabled"
            )
        policy = replace(
            command.policy,
            seed_urls=(),
            sitemap_urls=(),
            max_pages=self._manifest_safety_ceiling,
            follow_internal_links=False,
            discover_sitemaps=False,
            primary_language=manifest.primary_language,
            translated_locales=manifest.translated_locales,
            manifest_fingerprint=manifest.fingerprint,
            validators=(),
        )
        job = WebSyncJob(
            tenant_id=command.principal.tenant_id,
            job_id=str(uuid4()),
            site_id=site_id,
            base_url=base_url,
            status=WebSyncJobStatus.PREPARING,
            trigger=command.trigger,
            mode=command.mode,
            publication_status=(
                WebSyncPublicationStatus.NOT_REQUESTED
                if command.mode is WebSyncMode.SHADOW
                else WebSyncPublicationStatus.PENDING
            ),
            manifest_id=manifest_id,
            sample_size=command.sample_size if command.mode is WebSyncMode.SHADOW else None,
            policy=policy,
            requested_by=command.principal.subject_id,
            correlation_id=command.correlation_id,
            idempotency_key=idempotency_key,
            requested_at=datetime.now(UTC),
            manifest_version=manifest.version,
            manifest_fingerprint=manifest.fingerprint,
            phase=WebSyncJobPhase.PREPARING,
            expected_count=selected_count,
        )
        try:
            queued, created = await self._store.enqueue(
                job,
                expected_source_config_version=manifest.source_config_version,
            )
        except WebSyncSourceConfigVersionConflictError as exc:
            raise ValueError("sitemap source configuration changed; run preflight again") from exc
        return EnqueueWebSyncJobResult(queued, created)

    async def list(self, query: ListWebSyncJobsQuery) -> ListWebSyncJobsResult:
        _require_scope(query.principal.scopes, "knowledge:read")
        limit = max(1, min(query.limit, 100))
        return ListWebSyncJobsResult(
            await self._store.list_recent(
                tenant_id=query.principal.tenant_id,
                site_id=query.site_id.strip() if query.site_id else None,
                limit=limit,
            )
        )

    async def get(self, query: GetWebSyncJobQuery) -> WebSyncJob:
        _require_scope(query.principal.scopes, "knowledge:read")
        job = await self._store.get(
            tenant_id=query.principal.tenant_id,
            job_id=query.job_id,
        )
        if job is None:
            raise LookupError("website sync job was not found")
        return job

    async def cancel(self, command: CancelWebSyncJobCommand) -> WebSyncJob:
        _require_scope(command.principal.scopes, "knowledge:sync")
        job = await self._store.get(
            tenant_id=command.principal.tenant_id,
            job_id=command.job_id,
        )
        if job is None:
            raise LookupError("website sync job was not found")
        return await self._store.request_cancel(
            tenant_id=command.principal.tenant_id,
            job_id=command.job_id,
            requested_by=command.principal.subject_id,
            requested_at=datetime.now(UTC),
        )

    async def retry(self, command: RetryWebSyncJobCommand) -> WebSyncJob:
        _require_scope(command.principal.scopes, "knowledge:sync")
        return await self._store.retry_blocked(
            tenant_id=command.principal.tenant_id,
            job_id=command.job_id,
            requested_by=command.principal.subject_id,
            requested_at=datetime.now(UTC),
        )

    async def reconcile_stale_versions(
        self,
        command: ReconcileStaleWebSyncJobCommand,
    ) -> WebSyncJob:
        _require_scope(command.principal.scopes, "knowledge:sync")
        job = await self._store.get(
            tenant_id=command.principal.tenant_id,
            job_id=command.job_id,
        )
        if job is None:
            raise LookupError("website sync job was not found")
        if job.status is not WebSyncJobStatus.BLOCKED or not (
            job.report and "stale_version_reference" in job.report.publication_block_reasons
        ):
            raise ValueError("website sync job does not require stale version reconciliation")
        if self._knowledge_control_plane is None:
            raise RuntimeError("knowledge version reconciliation is unavailable")
        referenced_ids = await self._store.list_referenced_version_ids(
            tenant_id=command.principal.tenant_id,
            job_id=command.job_id,
        )
        stale_ids = await self._knowledge_control_plane.invalid_site_publication_version_ids(
            tenant_id=command.principal.tenant_id,
            site_id=job.site_id,
            publication_id=job.job_id,
            version_ids=referenced_ids,
        )
        if not stale_ids:
            raise ValueError("stale version references were not found; retry finalization instead")
        return await self._store.reconcile_stale_versions(
            tenant_id=command.principal.tenant_id,
            job_id=command.job_id,
            version_ids=stale_ids,
            requested_by=command.principal.subject_id,
            requested_at=datetime.now(UTC),
        )

    async def list_items(
        self,
        query: ListWebSyncJobItemsQuery,
    ) -> ListWebSyncJobItemsResult:
        _require_scope(query.principal.scopes, "knowledge:read")
        job = await self._store.get(
            tenant_id=query.principal.tenant_id,
            job_id=query.job_id,
        )
        if job is None:
            raise LookupError("website sync job was not found")
        return ListWebSyncJobItemsResult(
            await self._store.list_items(
                tenant_id=query.principal.tenant_id,
                job_id=query.job_id,
                status=query.status,
                limit=max(1, min(query.limit, 500)),
                offset=max(0, query.offset),
            )
        )

    @property
    def production_available(self) -> bool:
        return self._production_enabled and self._production_pipeline_ready

    @property
    def production_blocking_reasons(self) -> tuple[str, ...]:
        reasons: list[str] = []
        if not self._production_pipeline_ready:
            reasons.append("production_pipeline_not_ready")
        if not self._production_enabled:
            reasons.append("operator_flag_disabled")
        return tuple(reasons)


def _require_scope(scopes: frozenset[str], required: str) -> None:
    if required not in scopes:
        raise PermissionError(f"missing required scope: {required}")


def _selected_count(
    item_count: int,
    mode: WebSyncMode,
    sample_size: int | None,
    *,
    full_shadow_enabled: bool,
) -> int:
    if mode is WebSyncMode.PRODUCTION:
        if sample_size is not None:
            raise ValueError("production synchronization does not accept a sample size")
        return item_count
    if sample_size is None:
        if not full_shadow_enabled:
            raise ValueError("full shadow synchronization is disabled")
        return item_count
    if sample_size not in _SHADOW_SAMPLE_SIZES:
        raise ValueError("shadow sample size must be one of 20, 100, 200, or 500")
    if sample_size > item_count:
        raise ValueError("shadow sample size exceeds the manifest URL count")
    return sample_size
