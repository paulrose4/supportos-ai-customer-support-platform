import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.dependencies import authenticate_admin_request, get_container
from app.api.schemas import (
    EnqueueWebSyncJobResponse,
    KnowledgeSyncResponse,
    ListWebSyncJobItemsResponse,
    ListWebSyncJobsResponse,
    SiteKnowledgeReadinessResponse,
    WebCrawlManifestResponse,
    WebCrawlPreflightRequest,
    WebKnowledgeSyncRequest,
    WebKnowledgeSyncResponse,
    WebSyncAvailabilityResponse,
    WebSyncCapabilitiesResponse,
    WebSyncCapabilityDecisionResponse,
    WebSyncJobActionResponse,
    WebSyncJobCreateRequest,
    WebSyncJobItemResponse,
    WebSyncJobReportResponse,
    WebSyncJobResponse,
    WebSyncWorkerAvailabilityResponse,
)
from app.application.dto import (
    CancelWebSyncJobCommand,
    EnqueueWebSyncJobCommand,
    GetLatestWebCrawlManifestQuery,
    GetSiteKnowledgeReadinessQuery,
    GetSiteWebSourceConfigQuery,
    GetWebSyncJobQuery,
    ListSitesQuery,
    ListWebSyncJobItemsQuery,
    ListWebSyncJobsQuery,
    ReconcileStaleWebSyncJobCommand,
    RetryWebSyncJobCommand,
    RunWebCrawlPreflightCommand,
)
from app.application.services.web_sync_capability import WebSyncWorkerCapability
from app.application.tenant_context import global_knowledge_scope
from app.bootstrap.container import Container
from app.domain.models import (
    SiteWebDiscoveryMode,
    SiteWebSourceValidationStatus,
    WebCrawlManifest,
    WebSyncJob,
    WebSyncJobItem,
    WebSyncJobItemStatus,
    WebSyncJobPhase,
    WebSyncJobStatus,
    WebSyncMode,
    WebSyncPolicySnapshot,
)
from app.guardrails import require_scope

router = APIRouter(prefix="/v1/knowledge", tags=["knowledge"])


@router.get(
    "/web-sync-availability/{site_id}",
    response_model=WebSyncAvailabilityResponse,
)
async def get_web_sync_availability(
    site_id: str,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> WebSyncAvailabilityResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        sites = await container.support_operations_service.list_sites(ListSitesQuery(principal))
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    site = next((item for item in sites.items if item.site_id == site_id), None)
    if site is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site not found")

    crawler_enabled = container.settings.web_crawler_enabled
    base_url_configured = bool(site.base_url.strip())
    worker = await _web_sync_worker_capability(container, principal)
    worker_status = "not_required" if not crawler_enabled else worker.status
    preflight_blocking_reasons: list[str] = []
    if not crawler_enabled:
        preflight_blocking_reasons.append("crawler_disabled")
    if site.status != "active":
        preflight_blocking_reasons.append("site_inactive")
    if site.verification_status != "verified":
        preflight_blocking_reasons.append("site_not_verified")
    if not base_url_configured:
        preflight_blocking_reasons.append("site_base_url_missing")

    blocking_reasons = list(preflight_blocking_reasons)
    if crawler_enabled and not worker.processing_ready:
        if getattr(container, "web_sync_capability_service", None) is None:
            blocking_reasons.append("web_sync_worker_unavailable")
        else:
            blocking_reasons.extend(worker.reason_codes)
    preflight_ready = not preflight_blocking_reasons
    job_processing_ready = preflight_ready and (not crawler_enabled or worker.processing_ready)
    heartbeat_age_seconds = (
        max(0.0, (worker.observed_at - worker.last_heartbeat_at).total_seconds())
        if worker.last_heartbeat_at is not None
        else None
    )
    worker_response = WebSyncWorkerAvailabilityResponse(
        status=worker_status,
        freshness=worker.freshness,
        coverage=worker.coverage,
        tenant_covered=worker.coverage == "covered",
        last_heartbeat_at=worker.last_heartbeat_at,
        heartbeat_expires_at=worker.heartbeat_expires_at,
        heartbeat_age_seconds=heartbeat_age_seconds,
        covered_instance_count=worker.covered_instance_count,
        available_instance_count=worker.available_instance_count,
    )
    preflight_decision = WebSyncCapabilityDecisionResponse(
        allowed=preflight_ready,
        reason_codes=preflight_blocking_reasons,
    )
    shadow_decision = WebSyncCapabilityDecisionResponse(
        allowed=job_processing_ready,
        reason_codes=[] if job_processing_ready else blocking_reasons,
    )
    production_reasons = list(blocking_reasons)
    if not container.web_sync_job_service.production_available:
        production_reasons.extend(container.web_sync_job_service.production_blocking_reasons)
    production_decision = WebSyncCapabilityDecisionResponse(
        allowed=job_processing_ready and container.web_sync_job_service.production_available,
        reason_codes=list(dict.fromkeys(production_reasons)),
    )
    return WebSyncAvailabilityResponse(
        site_id=site.site_id,
        crawler_enabled=crawler_enabled,
        site_status=site.status,
        site_verification_status=site.verification_status,
        base_url_configured=base_url_configured,
        worker_status=worker_status,
        max_pages=container.settings.web_crawler_manifest_safety_ceiling,
        manifest_safety_ceiling=container.settings.web_crawler_manifest_safety_ceiling,
        full_shadow_enabled=container.settings.web_crawler_full_shadow_enabled,
        prepare_batch_size=container.settings.web_sync_prepare_batch_size,
        worker_concurrency=container.settings.web_sync_worker_concurrency,
        domain_concurrency=container.settings.web_sync_domain_concurrency,
        preflight_ready=preflight_ready,
        job_processing_ready=job_processing_ready,
        blocking_reasons=list(dict.fromkeys(blocking_reasons)),
        observed_at=worker.observed_at,
        valid_until=worker.heartbeat_expires_at,
        worker_heartbeat_at=worker.last_heartbeat_at,
        worker_heartbeat_age_seconds=heartbeat_age_seconds,
        worker_tenant_covered=worker.coverage == "covered",
        worker=worker_response,
        capabilities=WebSyncCapabilitiesResponse(
            preflight=preflight_decision,
            enqueue_shadow=shadow_decision,
            enqueue_production=production_decision,
        ),
    )


@router.get(
    "/readiness/{site_id}",
    response_model=SiteKnowledgeReadinessResponse,
)
async def get_site_knowledge_readiness(
    site_id: str,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> SiteKnowledgeReadinessResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        sites = await container.support_operations_service.list_sites(ListSitesQuery(principal))
        site = next((item for item in sites.items if item.site_id == site_id), None)
        if site is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site not found")
        result = await container.site_knowledge_readiness_service.get(
            GetSiteKnowledgeReadinessQuery(
                principal=principal,
                site_id=site_id,
                language=site.primary_language,
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return SiteKnowledgeReadinessResponse(
        site_id=result.site_id,
        ready=result.ready,
        catalog_ready=result.catalog_ready,
        policy_ready=result.policy_ready,
        care_ready=result.care_ready,
        active_product_count=result.active_product_count,
        active_document_count=result.active_document_count,
        active_snapshot_id=result.active_snapshot_id,
        last_successful_sync_at=result.last_successful_sync_at,
        blocking_reasons=list(result.blocking_reasons),
    )


@router.post("/sync", response_model=KnowledgeSyncResponse)
async def sync_knowledge(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> KnowledgeSyncResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        require_scope(principal, "knowledge:sync")
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    report = await container.knowledge_sync_service.sync(principal.tenant_id)
    return KnowledgeSyncResponse(
        sync_job_id=report.sync_job_id,
        discovered_count=report.discovered_count,
        indexed_count=report.indexed_count,
        skipped_count=report.skipped_count,
        excluded_count=report.excluded_count,
        failed_count=report.failed_count,
        errors=report.errors,
    )


@router.post("/sync/global", response_model=KnowledgeSyncResponse)
async def sync_global_knowledge(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> KnowledgeSyncResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        require_scope(principal, "knowledge:sync:global")
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    if container.global_knowledge_sync_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="global knowledge synchronization is unavailable",
        )

    with global_knowledge_scope():
        report = await container.global_knowledge_sync_service.sync("__global__")
    return KnowledgeSyncResponse(
        sync_job_id=report.sync_job_id,
        discovered_count=report.discovered_count,
        indexed_count=report.indexed_count,
        skipped_count=report.skipped_count,
        excluded_count=report.excluded_count,
        failed_count=report.failed_count,
        errors=report.errors,
    )


@router.post("/sync/web/{site_id}", response_model=WebKnowledgeSyncResponse)
async def sync_web_knowledge(
    site_id: str,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
    payload: WebKnowledgeSyncRequest | None = None,
) -> WebKnowledgeSyncResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        require_scope(principal, "knowledge:sync")
        sites = await container.support_operations_service.list_sites(ListSitesQuery(principal))
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    if not container.settings.web_crawler_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="website knowledge synchronization is disabled",
        )

    site = next((item for item in sites.items if item.site_id == site_id), None)
    if site is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="site not found")
    if site.status != "active" or site.verification_status != "verified":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="site must be active and verified",
        )
    if not site.base_url.strip():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="site has no base URL")

    del payload
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            "direct website synchronization is disabled; run a preflight and enqueue a manifest job"
        ),
    )


@router.post(
    "/web-crawl-preflights/{site_id}",
    response_model=WebCrawlManifestResponse,
)
async def run_web_crawl_preflight(
    site_id: str,
    payload: WebCrawlPreflightRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> WebCrawlManifestResponse:
    principal = await authenticate_admin_request(request, container)
    if not container.settings.web_crawler_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="website knowledge synchronization is disabled",
        )
    try:
        sites = await container.support_operations_service.list_sites(ListSitesQuery(principal))
        site = next((item for item in sites.items if item.site_id == site_id), None)
        if site is None:
            raise LookupError("site not found")
        if (
            site.status != "active"
            or site.verification_status != "verified"
            or not site.base_url.strip()
        ):
            raise ValueError("site must be active, verified, and have a base URL")
        source_result = await container.site_web_source_config_service.get_config(
            GetSiteWebSourceConfigQuery(principal=principal, site_id=site.site_id)
        )
        source_config = source_result.config
        result = await container.web_crawl_preflight_service.run(
            RunWebCrawlPreflightCommand(
                principal=principal,
                site_id=site.site_id,
                base_url=site.base_url,
                primary_language=site.primary_language,
                translation_provider=payload.translation_provider,
                discovery_mode=source_config.discovery_mode.value,
                explicit_sitemap_urls=source_config.explicit_sitemap_urls,
                allowed_sitemap_origins=source_result.allowed_sitemap_origins,
                source_config_version=source_config.config_version,
            )
        )
        if (
            source_config.explicit_sitemap_urls
            and source_config.discovery_mode is not SiteWebDiscoveryMode.AUTO
        ):
            validation_status = (
                SiteWebSourceValidationStatus.VALID
                if result.manifest.discovery_method == "manual"
                and result.manifest.status.value == "ready"
                else SiteWebSourceValidationStatus.INVALID
            )
            validated_config = (
                await container.site_web_source_config_service.mark_validation_status(
                    principal=principal,
                    site_id=site.site_id,
                    expected_config_version=source_config.config_version,
                    validation_status=validation_status,
                    validated_at=datetime.now(UTC),
                )
            )
            if validated_config is None:
                raise ValueError(
                    "sitemap source configuration changed during preflight; run preflight again"
                )
        current_source = await container.site_web_source_config_service.get_config(
            GetSiteWebSourceConfigQuery(principal=principal, site_id=site.site_id)
        )
        if current_source.config.config_version != source_config.config_version:
            raise ValueError(
                "sitemap source configuration changed during preflight; run preflight again"
            )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _web_crawl_manifest_response(
        container,
        result.manifest,
        current_source_config_version=source_config.config_version,
    )


@router.get(
    "/web-crawl-preflights/{site_id}/latest",
    response_model=WebCrawlManifestResponse | None,
)
async def get_latest_web_crawl_manifest(
    site_id: str,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> WebCrawlManifestResponse | None:
    principal = await authenticate_admin_request(request, container)
    try:
        manifest = await container.web_crawl_preflight_service.get_latest(
            GetLatestWebCrawlManifestQuery(principal=principal, site_id=site_id)
        )
        source_result = (
            None
            if manifest is None
            else await container.site_web_source_config_service.get_config(
                GetSiteWebSourceConfigQuery(principal=principal, site_id=site_id)
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return (
        None
        if manifest is None
        else _web_crawl_manifest_response(
            container,
            manifest,
            current_source_config_version=source_result.config.config_version,
        )
    )


@router.post(
    "/web-sync-jobs/{site_id}",
    response_model=EnqueueWebSyncJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def enqueue_web_sync_job(
    site_id: str,
    payload: WebSyncJobCreateRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> EnqueueWebSyncJobResponse:
    principal = await authenticate_admin_request(request, container)
    if not container.settings.web_crawler_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="website knowledge synchronization is disabled",
        )
    try:
        sites = await container.support_operations_service.list_sites(ListSitesQuery(principal))
        site = next((item for item in sites.items if item.site_id == site_id), None)
        if site is None:
            raise LookupError("site not found")
        if site.status != "active" or site.verification_status != "verified":
            raise ValueError("site must be active and verified")
        if not site.base_url.strip():
            raise ValueError("site has no base URL")
        worker = await _web_sync_worker_capability(container, principal)
        if not worker.processing_ready:
            legacy_worker_gate = getattr(container, "web_sync_capability_service", None) is None
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "website knowledge synchronization worker is not healthy"
                    if legacy_worker_gate
                    else {
                        "code": next(
                            iter(worker.reason_codes),
                            "web_sync_worker_unavailable",
                        ),
                        "message": (
                            "website knowledge synchronization worker is not available "
                            "for this tenant"
                        ),
                    }
                ),
                headers={"Retry-After": "30"},
            )
        result = await container.web_sync_job_service.enqueue(
            EnqueueWebSyncJobCommand(
                principal=principal,
                site_id=site.site_id,
                base_url=site.base_url,
                primary_language=site.primary_language,
                policy=_web_sync_policy(container, site),
                manifest_id=payload.manifest_id,
                mode=WebSyncMode(payload.mode),
                sample_size=payload.sample_size,
                idempotency_key=payload.idempotency_key,
                correlation_id=request.state.correlation_id,
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return EnqueueWebSyncJobResponse(created=result.created, job=_web_sync_job_response(result.job))


@router.get("/web-sync-jobs", response_model=ListWebSyncJobsResponse)
async def list_web_sync_jobs(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
    site_id: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> ListWebSyncJobsResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await container.web_sync_job_service.list(
            ListWebSyncJobsQuery(principal=principal, site_id=site_id, limit=limit)
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return ListWebSyncJobsResponse(items=[_web_sync_job_response(item) for item in result.items])


@router.get("/web-sync-jobs/{job_id}", response_model=WebSyncJobResponse)
async def get_web_sync_job(
    job_id: str,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> WebSyncJobResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        job = await container.web_sync_job_service.get(
            GetWebSyncJobQuery(principal=principal, job_id=job_id)
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _web_sync_job_response(job)


@router.post("/web-sync-jobs/{job_id}/cancel", response_model=WebSyncJobResponse)
async def cancel_web_sync_job(
    job_id: str,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> WebSyncJobResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        job = await container.web_sync_job_service.cancel(
            CancelWebSyncJobCommand(principal=principal, job_id=job_id)
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _web_sync_job_response(job)


@router.post("/web-sync-jobs/{job_id}/retry", response_model=WebSyncJobResponse)
async def retry_web_sync_job(
    job_id: str,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> WebSyncJobResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        job = await container.web_sync_job_service.retry(
            RetryWebSyncJobCommand(principal=principal, job_id=job_id)
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _web_sync_job_response(job)


@router.post(
    "/web-sync-jobs/{job_id}/reconcile-stale-versions",
    response_model=WebSyncJobResponse,
)
async def reconcile_stale_web_sync_versions(
    job_id: str,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> WebSyncJobResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        job = await container.web_sync_job_service.reconcile_stale_versions(
            ReconcileStaleWebSyncJobCommand(principal=principal, job_id=job_id)
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _web_sync_job_response(job)


@router.get(
    "/web-sync-jobs/{job_id}/items",
    response_model=ListWebSyncJobItemsResponse,
)
async def list_web_sync_job_items(
    job_id: str,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
    item_status: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> ListWebSyncJobItemsResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        parsed_status = WebSyncJobItemStatus(item_status) if item_status else None
        result = await container.web_sync_job_service.list_items(
            ListWebSyncJobItemsQuery(
                principal=principal,
                job_id=job_id,
                status=parsed_status,
                limit=limit,
                offset=offset,
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return ListWebSyncJobItemsResponse(
        items=[_web_sync_job_item_response(item) for item in result.items],
        offset=offset,
        next_offset=offset + len(result.items) if len(result.items) == limit else None,
    )


def _web_sync_policy(container: Container, site: object | None = None) -> WebSyncPolicySnapshot:
    settings = container.settings
    site_policy = getattr(site, "duplicate_product_policy", None)
    site_order = getattr(site, "duplicate_product_order", None)
    return WebSyncPolicySnapshot(
        max_pages=settings.web_crawler_manifest_safety_ceiling,
        max_sitemaps=settings.web_crawler_max_sitemaps,
        max_response_bytes=settings.web_crawler_max_response_bytes,
        max_decompressed_response_bytes=settings.web_crawler_max_decompressed_response_bytes,
        max_compression_ratio=settings.web_crawler_max_compression_ratio,
        request_timeout_seconds=settings.web_crawler_request_timeout_seconds,
        crawl_delay_seconds=settings.web_crawler_delay_seconds,
        follow_internal_links=settings.web_crawler_follow_internal_links,
        respect_robots_txt=settings.web_crawler_respect_robots_txt,
        batch_size=settings.web_crawler_batch_size,
        duplicate_product_policy=site_policy or settings.duplicate_product_policy,
        duplicate_product_order=site_order or settings.duplicate_product_order,
    )


async def _web_sync_worker_capability(
    container: Container,
    principal: object,
) -> WebSyncWorkerCapability:
    service = getattr(container, "web_sync_capability_service", None)
    if service is not None:
        return await service.execute(principal)  # type: ignore[arg-type]
    # Compatibility path for test containers and older deployments during the
    # migration rollout. New production containers always use the registry.
    status_value = _web_sync_worker_status(container)
    now = datetime.now(UTC)
    if status_value == "healthy":
        heartbeat_path = container.settings.web_sync_worker_heartbeat_path.strip()
        try:
            heartbeat_stat = await asyncio.to_thread(Path(heartbeat_path).stat)
            heartbeat_at = datetime.fromtimestamp(heartbeat_stat.st_mtime, UTC)
        except OSError:
            heartbeat_at = None
        return WebSyncWorkerCapability(
            status="healthy",
            freshness="fresh",
            coverage="covered",
            observed_at=now,
            last_heartbeat_at=heartbeat_at,
            heartbeat_expires_at=now + timedelta(seconds=60),
            covered_instance_count=1,
            available_instance_count=1,
            reason_codes=(),
        )
    return WebSyncWorkerCapability(
        status=status_value,
        freshness="unknown" if status_value == "unknown" else "expired",
        coverage="unknown",
        observed_at=now,
        last_heartbeat_at=None,
        heartbeat_expires_at=None,
        covered_instance_count=0,
        available_instance_count=0,
        reason_codes=("web_sync_worker_unavailable",),
    )


def _web_sync_worker_status(container: Container) -> str:
    settings = container.settings
    if not settings.web_crawler_enabled:
        return "not_required"
    heartbeat_path = settings.web_sync_worker_heartbeat_path.strip()
    if not heartbeat_path:
        return "unknown"
    try:
        age_seconds = datetime.now(UTC).timestamp() - Path(heartbeat_path).stat().st_mtime
    except OSError:
        return "unavailable"
    return "healthy" if age_seconds <= 60 else "unavailable"


def _web_sync_job_response(job: WebSyncJob) -> WebSyncJobResponse:
    report = job.report
    report_response = None
    if report is not None:
        report_response = WebSyncJobReportResponse(
            pipeline_sync_job_id=report.pipeline_sync_job_id,
            published=report.published,
            discovered_count=report.discovered_count,
            document_count=report.document_count,
            changed_document_count=report.changed_document_count,
            unchanged_document_count=report.unchanged_document_count,
            http_not_modified_count=report.http_not_modified_count,
            duplicate_count=report.duplicate_count,
            duplicate_product_count=report.duplicate_product_count,
            product_count=report.product_count,
            pending_removal_count=report.pending_removal_count,
            expired_count=report.expired_count,
            indexed_chunk_count=report.indexed_chunk_count,
            excluded_count=report.excluded_count,
            failed_count=report.failed_count,
            duplicate_product_total=report.duplicate_product_total,
            duplicate_product_excluded_count=report.duplicate_product_excluded_count,
            duplicate_product_conflict_warning_count=report.duplicate_product_conflict_warning_count,
            duplicate_product_unresolved_count=report.duplicate_product_unresolved_count,
            winner_product_count=report.winner_product_count,
            errors=report.errors,
            processed_page_count=(report.processed_page_count or job.completed_count),
            produced_document_count=(report.produced_document_count or report.document_count),
            failed_page_count=(report.failed_page_count or job.failed_item_count),
            unresolved_product_identity_count=(
                report.unresolved_product_identity_count
                or report.duplicate_product_unresolved_count
            ),
            blocking_issue_count=report.blocking_issue_count,
            publication_block_reasons=list(report.publication_block_reasons),
        )
    now = datetime.now(UTC)
    execution_state, stale, stale_at, reason_code, next_wake_at = _web_sync_execution_state(
        job,
        now=now,
    )
    actions = _web_sync_job_actions(job)
    return WebSyncJobResponse(
        job_id=job.job_id,
        site_id=job.site_id,
        base_url=job.base_url,
        status=job.status.value,
        trigger=job.trigger.value,
        mode=job.mode.value,
        publication_status=job.publication_status.value,
        manifest_id=job.manifest_id,
        sample_size=job.sample_size,
        phase=job.phase.value,
        manifest_version=job.manifest_version,
        manifest_fingerprint=job.manifest_fingerprint,
        prepared_count=job.prepared_count,
        expected_count=job.expected_count,
        completed_count=job.completed_count,
        succeeded_count=job.succeeded_count,
        not_modified_count=job.not_modified_count,
        excluded_item_count=job.excluded_item_count,
        failed_item_count=job.failed_item_count,
        canceled_item_count=job.canceled_item_count,
        requested_by=job.requested_by,
        requested_at=job.requested_at,
        started_at=job.started_at,
        heartbeat_at=job.heartbeat_at,
        completed_at=job.completed_at,
        cancel_requested_at=job.cancel_requested_at,
        blocked_at=job.blocked_at,
        retention_expires_at=job.retention_expires_at,
        attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,
        max_pages=job.policy.max_pages,
        duplicate_product_policy=job.policy.duplicate_product_policy,
        duplicate_product_order=job.policy.duplicate_product_order,
        error_code=job.error_code,
        error_message=job.error_message,
        report=report_response,
        state_version=max(1, job.state_version),
        updated_at=job.updated_at or job.heartbeat_at or job.requested_at,
        last_progress_at=job.last_progress_at or job.heartbeat_at or job.started_at,
        available_at=job.available_at,
        prepare_stage=job.prepare_stage,
        prepare_cursor=job.prepare_cursor,
        claim_count=job.claim_count or job.attempt_count,
        failure_attempt_count=job.failure_attempt_count,
        yield_count=job.yield_count,
        execution_state=execution_state,
        stale=stale,
        stale_at=stale_at,
        reason_code=reason_code,
        next_wake_at=next_wake_at,
        actions=actions,
    )


def _web_sync_execution_state(
    job: WebSyncJob,
    *,
    now: datetime,
) -> tuple[str, bool, datetime | None, str | None, datetime | None]:
    if job.status in {
        WebSyncJobStatus.SUCCEEDED,
        WebSyncJobStatus.FAILED,
        WebSyncJobStatus.CANCELED,
    }:
        return "terminal", False, None, None, None
    if job.status is WebSyncJobStatus.CLEANUP_PENDING:
        return (
            "recovery",
            False,
            None,
            "staging_cleanup_pending",
            job.available_at,
        )
    if job.status is WebSyncJobStatus.BLOCKED:
        return "attention_required", False, None, "remediation_required", job.retention_expires_at
    if job.phase is WebSyncJobPhase.FINALIZING:
        state = "finalizing"
    elif job.status is WebSyncJobStatus.PREPARING:
        state = "waiting_for_worker" if job.lease_owner is None else "preparing"
    elif job.status is WebSyncJobStatus.QUEUED:
        if job.available_at is not None and job.available_at > now:
            state = "waiting_retry"
        else:
            state = "waiting_for_worker"
    else:
        state = "processing"
    if job.lease_expires_at is not None and job.lease_expires_at <= now:
        state = "recovery_pending"
    last_progress = job.last_progress_at or job.heartbeat_at or job.started_at
    stale_at = last_progress + timedelta(minutes=10) if last_progress is not None else None
    stale = bool(
        stale_at is not None
        and stale_at <= now
        and state not in {"waiting_retry", "finalizing", "terminal"}
    )
    if stale and state not in {"recovery_pending", "attention_required"}:
        state = "stalled"
    reason = "worker_lease_expired" if state == "recovery_pending" else None
    return state, stale, stale_at, reason, job.available_at


def _web_sync_job_actions(job: WebSyncJob) -> dict[str, WebSyncJobActionResponse]:
    terminal = job.status in {
        WebSyncJobStatus.SUCCEEDED,
        WebSyncJobStatus.FAILED,
        WebSyncJobStatus.CANCELED,
    }
    cancel_allowed = (
        not terminal
        and job.status is not WebSyncJobStatus.CLEANUP_PENDING
        and job.phase is not WebSyncJobPhase.FINALIZING
    )
    identity_reconciliation_required = bool(
        job.report
        and (
            {
                "unresolved_product_identity",
                "duplicate_winner_invariant_violation",
            }.intersection(job.report.publication_block_reasons)
            or any(
                "product_identity_conflict:" in message for message in job.report.errors.values()
            )
        )
    )
    retry_failed = (
        job.status is WebSyncJobStatus.BLOCKED
        and job.failed_item_count > 0
        and not identity_reconciliation_required
    )
    retry_finalization = job.status is WebSyncJobStatus.BLOCKED and bool(
        job.report
        and "finalization" in job.report.errors
        and "stale_version_reference" not in job.report.publication_block_reasons
    )
    reconcile_stale_versions = bool(
        job.status is WebSyncJobStatus.BLOCKED
        and job.report
        and "stale_version_reference" in job.report.publication_block_reasons
    )
    return {
        "cancel": WebSyncJobActionResponse(
            allowed=cancel_allowed,
            reason_codes=[] if cancel_allowed else ["job_terminal_or_finalizing"],
        ),
        "retry_failed": WebSyncJobActionResponse(
            allowed=retry_failed,
            reason_codes=(
                []
                if retry_failed
                else [
                    "product_identity_reconciliation_required"
                    if identity_reconciliation_required
                    else "no_failed_pages"
                ]
            ),
        ),
        "retry_finalization": WebSyncJobActionResponse(
            allowed=retry_finalization,
            reason_codes=(
                []
                if retry_finalization
                else [
                    "stale_version_reconciliation_required"
                    if job.report
                    and "stale_version_reference" in job.report.publication_block_reasons
                    else "finalization_not_retryable"
                ]
            ),
        ),
        "reconcile_stale_versions": WebSyncJobActionResponse(
            allowed=reconcile_stale_versions,
            reason_codes=(
                [] if reconcile_stale_versions else ["stale_version_reconciliation_not_required"]
            ),
        ),
        "abandon": WebSyncJobActionResponse(
            allowed=job.status is WebSyncJobStatus.BLOCKED,
            reason_codes=[] if job.status is WebSyncJobStatus.BLOCKED else ["job_not_blocked"],
        ),
    }


def _web_sync_job_item_response(item: WebSyncJobItem) -> WebSyncJobItemResponse:
    return WebSyncJobItemResponse(
        item_id=item.item_id,
        ordinal=item.ordinal,
        url=item.url,
        source_sitemap_url=item.source_sitemap_url,
        content_kind=item.content_kind,
        status=item.status.value,
        attempt_count=item.attempt_count,
        max_attempts=item.max_attempts,
        duration_ms=item.duration_ms,
        canonical_url=item.canonical_url,
        product_key=item.product_key,
        normalized_product_key=item.normalized_product_key,
        normalization_version=item.normalization_version,
        error_code=item.error_code,
        error_message=item.error_message,
        outcome_reason=item.outcome_reason,
        next_attempt_at=item.next_attempt_at,
        started_at=item.started_at,
        completed_at=item.completed_at,
        winner_item_id=item.winner_item_id,
        winner_url=item.winner_url,
        identity_source=item.identity_source,
        policy_version=item.policy_version,
    )


def _web_crawl_manifest_response(
    container: Container,
    manifest: WebCrawlManifest,
    *,
    current_source_config_version: int,
) -> WebCrawlManifestResponse:
    expires_at = manifest.created_at + timedelta(
        hours=container.settings.web_crawler_manifest_max_age_hours
    )
    return WebCrawlManifestResponse(
        manifest_id=manifest.manifest_id,
        site_id=manifest.site_id,
        base_url=manifest.base_url,
        root_sitemap_url=manifest.root_sitemap_url,
        root_sitemap_urls=list(manifest.root_sitemap_urls),
        discovery_method=manifest.discovery_method,
        warnings=list(manifest.warnings),
        coverage_status=manifest.coverage_status,
        discovery_attempts=[
            {
                "url": attempt.url,
                "source": attempt.source,
                "outcome": attempt.outcome,
                "final_url": attempt.final_url,
            }
            for attempt in manifest.discovery_attempts
        ],
        primary_language=manifest.primary_language,
        translation_provider=manifest.translation_provider,
        status=manifest.status.value,
        fingerprint=manifest.fingerprint,
        version=manifest.version,
        policy_version=manifest.policy_version,
        source_config_version=manifest.source_config_version,
        source_config_current=(manifest.source_config_version == current_source_config_version),
        primary_sitemap_urls=list(manifest.primary_sitemap_urls),
        translated_locales=list(manifest.translated_locales),
        excluded_sitemap_count=manifest.excluded_sitemap_count,
        excluded_url_count=manifest.excluded_url_count,
        url_count=manifest.url_count,
        content_kind_counts=manifest.content_kind_counts,
        blocking_reasons=list(manifest.blocking_reasons),
        production_sync_enabled=container.web_sync_job_service.production_available,
        production_blocking_reasons=(
            list(container.web_sync_job_service.production_blocking_reasons)
        ),
        expires_at=expires_at,
        is_expired=datetime.now(UTC) > expires_at,
        created_by=manifest.created_by,
        created_at=manifest.created_at,
    )
