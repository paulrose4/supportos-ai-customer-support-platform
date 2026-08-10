import argparse
import asyncio
import logging
import os
import random
import socket
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from app.application.dto import ReadinessResult
from app.application.tenant_context import tenant_scope
from app.bootstrap.container import build_container
from app.config import get_settings
from app.domain.models.web_sync_worker import WebSyncWorkerRegistration
from app.domain.ports.web_sync_workers import WebSyncWorkerRegistryPort
from app.knowledge.web.worker_coordinator import WebSyncWorkerCoordinator
from app.observability import configure_logging

logger = logging.getLogger(__name__)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the durable website synchronization worker.")
    parser.add_argument("--tenant-id", action="append", default=[])
    parser.add_argument("--worker-id")
    parser.add_argument("--once", action="store_true")
    return parser


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    if not settings.web_crawler_enabled:
        raise PermissionError("website synchronization is disabled; set WEB_CRAWLER_ENABLED=true")
    tenant_filter = tuple(dict.fromkeys(value.strip() for value in args.tenant_id if value.strip()))
    worker_id = args.worker_id or f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:12]}"
    container = await build_container(settings)
    functional_heartbeat: asyncio.Task[None] | None = None
    registry_heartbeat: asyncio.Task[None] | None = None
    covered_tenants: tuple[str, ...] = ()
    active_tenants: tuple[str, ...] = ()
    coverage_changed = asyncio.Event()
    registry = container.web_sync_worker_registry
    catalog = container.web_sync_tenant_catalog
    if registry is None or catalog is None:
        raise RuntimeError("website sync tenant catalog and worker registry are required")
    heartbeat_path_value = settings.web_sync_worker_heartbeat_path.strip()
    heartbeat_path = Path(heartbeat_path_value) if heartbeat_path_value else None
    worker_started_at = datetime.now(UTC)
    try:
        if heartbeat_path is not None:
            await asyncio.to_thread(heartbeat_path.unlink, missing_ok=True)
        await _initialize_knowledge_adapter(
            container.knowledge_adapter.initialize,
            max_delay_seconds=settings.qdrant_startup_retry_max_seconds,
        )
        if heartbeat_path is not None:
            functional_heartbeat = asyncio.create_task(
                _write_functional_heartbeat(
                    heartbeat_path,
                    container.readiness_service.execute,
                )
            )

        async def update_coverage(tenant_ids: tuple[str, ...]) -> None:
            nonlocal covered_tenants
            if covered_tenants == tenant_ids:
                return
            covered_tenants = tenant_ids
            coverage_changed.set()

        async def update_activity(tenant_ids: tuple[str, ...]) -> None:
            nonlocal active_tenants
            if active_tenants == tenant_ids:
                return
            active_tenants = tenant_ids
            # Wake the registry loop promptly so capacity telemetry reflects
            # admission changes without waiting for the next heartbeat tick.
            coverage_changed.set()

        registry_heartbeat = asyncio.create_task(
            _write_registry_heartbeat(
                registry,
                worker_instance_id=worker_id,
                started_at=worker_started_at,
                readiness_check=container.readiness_service.execute,
                covered_tenants=lambda: covered_tenants,
                active_tenants=lambda: active_tenants,
                heartbeat_seconds=settings.web_sync_worker_heartbeat_seconds,
                ttl_seconds=settings.web_sync_worker_registry_ttl_seconds,
                runtime_version=os.getenv("APP_VERSION") or os.getenv("GIT_SHA") or "unknown",
                coverage_changed=coverage_changed,
            )
        )
        coordinator = WebSyncWorkerCoordinator(
            catalog=catalog,
            runner=container.web_sync_job_worker,
            worker_instance_id=worker_id,
            max_active_tenants=settings.web_sync_max_active_tenants,
            quantum_pages=settings.web_sync_work_quantum_pages,
            quantum_seconds=settings.web_sync_work_quantum_seconds,
            tenant_watchdog_seconds=settings.web_sync_tenant_watchdog_seconds,
            poll_seconds=settings.web_sync_worker_poll_seconds,
            discovery_seconds=settings.web_sync_tenant_discovery_seconds,
            idle_backoff_max_seconds=settings.web_sync_idle_backoff_max_seconds,
            tenant_filter=tenant_filter,
            on_coverage=update_coverage,
            on_activity=update_activity,
        )
        if args.once:
            await coordinator.run_once()
            return 0
        await coordinator.run_forever()
    finally:
        if registry_heartbeat is not None:
            registry_heartbeat.cancel()
            await asyncio.gather(registry_heartbeat, return_exceptions=True)
        if registry is not None:
            await _unregister_worker(
                registry,
                worker_instance_id=worker_id,
                tenant_ids=covered_tenants,
                started_at=worker_started_at,
            )
        if functional_heartbeat is not None:
            functional_heartbeat.cancel()
            await asyncio.gather(functional_heartbeat, return_exceptions=True)
        await container.knowledge_adapter.close()
        if container.product_recommendation_index is not None:
            await container.product_recommendation_index.close()
        if container.redis_backend is not None:
            await container.redis_backend.close()
        await container.database.dispose()


async def _initialize_knowledge_adapter(
    initialize: Callable[[], Awaitable[None]],
    *,
    max_delay_seconds: float,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    jitter: Callable[[float, float], float] = random.uniform,
) -> None:
    delay_seconds = 1.0
    while True:
        try:
            await initialize()
            return
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            retry_seconds = min(
                max_delay_seconds,
                delay_seconds * jitter(0.8, 1.2),
            )
            logger.warning(
                "knowledge dependency is not ready; retrying (%s)",
                type(error).__name__,
                extra={
                    "dependency": "qdrant",
                    "error": type(error).__name__,
                    "retry_in_seconds": retry_seconds,
                },
            )
            await sleep(retry_seconds)
            delay_seconds = min(max_delay_seconds, delay_seconds * 2)


async def _write_functional_heartbeat(
    path: Path,
    readiness_check: Callable[[], Awaitable[ReadinessResult]],
    *,
    interval_seconds: float = 10,
) -> None:
    await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
    previous_failures: tuple[str, ...] | None = None
    while True:
        try:
            readiness = await readiness_check()
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            readiness = ReadinessResult(False, (type(error).__name__,))
        if readiness.is_ready:
            await asyncio.to_thread(path.touch)
        if readiness.failed_dependencies != previous_failures:
            log = logger.info if readiness.is_ready else logger.warning
            log(
                "web sync worker functional readiness changed",
                extra={"failed_dependencies": readiness.failed_dependencies},
            )
            previous_failures = readiness.failed_dependencies
        await asyncio.sleep(interval_seconds)


async def _write_registry_heartbeat(
    registry: WebSyncWorkerRegistryPort,
    *,
    worker_instance_id: str,
    started_at: datetime | None = None,
    readiness_check: Callable[[], Awaitable[ReadinessResult]],
    covered_tenants: Callable[[], tuple[str, ...]],
    active_tenants: Callable[[], tuple[str, ...]] | None = None,
    heartbeat_seconds: float,
    ttl_seconds: int,
    runtime_version: str,
    coverage_changed: asyncio.Event | None = None,
) -> None:
    registration_started_at = started_at or datetime.now(UTC)
    previously_registered: set[str] = set()
    while True:
        observed_at = datetime.now(UTC)
        try:
            readiness = await readiness_check()
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            readiness = ReadinessResult(False, (type(error).__name__,))
        tenant_ids = covered_tenants()
        active = set(tenant_ids)
        busy_tenants = set(active_tenants() if active_tenants is not None else ())
        for tenant_id in tenant_ids:
            registration = WebSyncWorkerRegistration(
                tenant_id=tenant_id,
                worker_instance_id=worker_instance_id,
                started_at=registration_started_at,
                last_seen_at=observed_at,
                expires_at=observed_at + timedelta(seconds=ttl_seconds),
                health_state="healthy" if readiness.is_ready else "unhealthy",
                runtime_version=runtime_version,
                # Registry rows are tenant-scoped: one worker instance has
                # one schedulable job slot for a given tenant.  The global
                # process budget is reported separately through metrics; it
                # must not make every tenant appear to have N local slots.
                capacity_total=1,
                capacity_in_use=int(tenant_id in busy_tenants),
                failure_codes=tuple(readiness.failed_dependencies),
            )
            try:
                with tenant_scope(tenant_id):
                    await registry.register_or_heartbeat(registration)
            except Exception as error:  # noqa: BLE001
                logger.warning(
                    "website sync worker registry heartbeat failed (%s)",
                    type(error).__name__,
                    extra={"tenant_id": tenant_id},
                )
        await _unregister_worker(
            registry,
            worker_instance_id=worker_instance_id,
            tenant_ids=tuple(previously_registered - active),
            started_at=registration_started_at,
        )
        previously_registered = active
        if coverage_changed is None:
            await asyncio.sleep(heartbeat_seconds)
        else:
            try:
                await asyncio.wait_for(coverage_changed.wait(), timeout=heartbeat_seconds)
            except TimeoutError:
                pass
            coverage_changed.clear()


async def _unregister_worker(
    registry: WebSyncWorkerRegistryPort,
    *,
    worker_instance_id: str,
    tenant_ids: tuple[str, ...],
    started_at: datetime | None = None,
) -> None:
    for tenant_id in tenant_ids:
        try:
            with tenant_scope(tenant_id):
                await registry.unregister(
                    tenant_id=tenant_id,
                    worker_instance_id=worker_instance_id,
                    started_at=started_at,
                )
        except Exception as error:  # noqa: BLE001
            logger.warning(
                "website sync worker unregister failed (%s)",
                type(error).__name__,
                extra={"tenant_id": tenant_id},
            )


def main() -> int:
    return asyncio.run(_run(_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
