import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from time import monotonic
from typing import Protocol
from uuid import uuid4

from app.application.tenant_context import tenant_scope

logger = logging.getLogger(__name__)


class ActiveTenantCatalog(Protocol):
    async def list_active_tenant_ids(self) -> tuple[str, ...]: ...


class TenantJobRunner(Protocol):
    async def run_once(
        self,
        *,
        tenant_id: str,
        worker_id: str,
        max_pages: int | None = None,
        max_seconds: float | None = None,
    ) -> bool: ...


@dataclass(slots=True)
class _TenantSchedule:
    idle_attempts: int = 0
    next_poll_at: float = 0.0


class WebSyncWorkerCoordinator:
    """Fairly schedules tenant-scoped queues without weakening tenant isolation."""

    def __init__(
        self,
        *,
        catalog: ActiveTenantCatalog,
        runner: TenantJobRunner,
        worker_instance_id: str,
        max_active_tenants: int,
        quantum_pages: int,
        quantum_seconds: float,
        tenant_watchdog_seconds: float | None = None,
        poll_seconds: float,
        discovery_seconds: float = 10.0,
        idle_backoff_max_seconds: float = 30.0,
        tenant_filter: Iterable[str] = (),
        on_coverage: Callable[[tuple[str, ...]], Awaitable[None]] | None = None,
        on_activity: Callable[[tuple[str, ...]], Awaitable[None]] | None = None,
    ) -> None:
        if not 1 <= max_active_tenants <= 32:
            raise ValueError("max active website sync tenants must be between 1 and 32")
        if not 1 <= quantum_pages <= 10_000:
            raise ValueError("website sync page quantum must be between 1 and 10000")
        if not 1 <= quantum_seconds <= 3600:
            raise ValueError("website sync time quantum must be between 1 and 3600 seconds")
        if tenant_watchdog_seconds is None:
            tenant_watchdog_seconds = quantum_seconds + 30
        if tenant_watchdog_seconds <= quantum_seconds:
            raise ValueError("website sync tenant watchdog must exceed the time quantum")
        if poll_seconds <= 0:
            raise ValueError("website sync poll interval must be positive")
        if discovery_seconds < 0:
            raise ValueError("website sync tenant discovery interval must be non-negative")
        if idle_backoff_max_seconds < poll_seconds:
            raise ValueError("idle backoff maximum must be at least the poll interval")
        self._catalog = catalog
        self._runner = runner
        self._worker_instance_id = worker_instance_id
        self._max_active_tenants = max_active_tenants
        self._quantum_pages = quantum_pages
        self._quantum_seconds = quantum_seconds
        self._tenant_watchdog_seconds = tenant_watchdog_seconds
        self._poll_seconds = poll_seconds
        self._discovery_seconds = discovery_seconds
        self._idle_backoff_max_seconds = idle_backoff_max_seconds
        self._tenant_filter = frozenset(
            value.strip()
            for value in tenant_filter
            if value.strip() and value.strip() != "__global__"
        )
        self._on_coverage = on_coverage
        self._on_activity = on_activity
        self._queue: deque[str] = deque()
        self._schedule: dict[str, _TenantSchedule] = {}
        self._cached_tenant_ids: tuple[str, ...] = ()
        self._next_discovery_at = 0.0
        self._active_tasks: dict[str, asyncio.Task[bool]] = {}

    async def run_once(self) -> bool:
        """Run one bounded scheduling wave, primarily for maintenance and tests."""

        return await self._run_scheduler_tick(drain=True)

    async def _run_scheduler_tick(self, *, drain: bool) -> bool:
        try:
            tenant_ids = await self._active_tenant_ids()
        except asyncio.CancelledError:
            raise
        except Exception:
            # A transient control-plane failure must not terminate the worker
            # process.  Drop coverage for admission purposes until discovery
            # succeeds again; stale tenant IDs must never be trusted forever.
            logger.exception("website sync tenant discovery failed")
            await self._notify(self._on_coverage, ())
            await self._notify(self._on_activity, tuple(self._active_tasks))
            return False
        await self._cancel_inactive_tasks(frozenset(tenant_ids))
        self._reconcile_queue(tenant_ids)
        await self._notify(self._on_coverage, tenant_ids)
        selected = self._select_ready_tenants(
            limit=max(0, self._max_active_tenants - len(self._active_tasks))
        )
        for tenant_id in selected:
            self._active_tasks[tenant_id] = asyncio.create_task(self._run_tenant(tenant_id))
        await self._notify(self._on_activity, tuple(self._active_tasks))
        if not self._active_tasks:
            return False
        await asyncio.wait(
            tuple(self._active_tasks.values()),
            timeout=None if drain else self._poll_seconds,
            return_when=(asyncio.ALL_COMPLETED if drain else asyncio.FIRST_COMPLETED),
        )
        processed = self._reap_completed_tasks()
        await self._notify(self._on_activity, tuple(self._active_tasks))
        return processed

    def _reap_completed_tasks(self) -> bool:
        processed = False
        completed = tuple(
            (tenant_id, task) for tenant_id, task in self._active_tasks.items() if task.done()
        )
        for tenant_id, task in completed:
            del self._active_tasks[tenant_id]
            if task.cancelled():
                continue
            result = task.exception()
            if isinstance(result, BaseException):
                if isinstance(result, asyncio.CancelledError):
                    raise result
                logger.exception(
                    "website sync tenant execution failed",
                    exc_info=(type(result), result, result.__traceback__),
                    extra={"tenant_id": tenant_id},
                )
                if tenant_id in self._schedule:
                    self._record_idle(tenant_id)
                continue
            if task.result():
                processed = True
                if tenant_id in self._schedule:
                    self._record_progress(tenant_id)
            elif tenant_id in self._schedule:
                self._record_idle(tenant_id)
        return processed

    async def _cancel_inactive_tasks(self, active_tenant_ids: frozenset[str]) -> None:
        inactive = tuple(
            (tenant_id, task)
            for tenant_id, task in self._active_tasks.items()
            if tenant_id not in active_tenant_ids
        )
        if not inactive:
            return
        for _tenant_id, task in inactive:
            task.cancel()
        await asyncio.gather(*(task for _tenant_id, task in inactive), return_exceptions=True)
        for tenant_id, _task in inactive:
            self._active_tasks.pop(tenant_id, None)

    @staticmethod
    async def _notify(
        callback: Callable[[tuple[str, ...]], Awaitable[None]] | None,
        tenant_ids: tuple[str, ...],
    ) -> None:
        if callback is None:
            return
        try:
            await callback(tenant_ids)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Registry telemetry is valuable, but it must not take down the
            # execution loop or leave leases orphaned when the registry is
            # temporarily unavailable.
            logger.exception("website sync worker state notification failed")

    async def run_forever(self) -> None:
        try:
            while True:
                processed = await self._run_scheduler_tick(drain=False)
                if not processed:
                    await asyncio.sleep(self._poll_seconds)
                else:
                    await asyncio.sleep(0)
        finally:
            tasks = tuple(self._active_tasks.values())
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._active_tasks.clear()
            await self._notify(self._on_activity, ())

    async def _active_tenant_ids(self) -> tuple[str, ...]:
        now = monotonic()
        if now < self._next_discovery_at:
            return self._cached_tenant_ids
        discovered = tuple(
            dict.fromkeys(
                value.strip()
                for value in await self._catalog.list_active_tenant_ids()
                if value.strip() and value.strip() != "__global__"
            )
        )
        if self._tenant_filter:
            discovered = tuple(value for value in discovered if value in self._tenant_filter)
        self._cached_tenant_ids = discovered
        self._next_discovery_at = now + self._discovery_seconds
        return discovered

    def _reconcile_queue(self, tenant_ids: tuple[str, ...]) -> None:
        active = set(tenant_ids)
        retained = [tenant_id for tenant_id in self._queue if tenant_id in active]
        retained_set = set(retained)
        retained.extend(tenant_id for tenant_id in tenant_ids if tenant_id not in retained_set)
        self._queue = deque(retained)
        for tenant_id in tenant_ids:
            self._schedule.setdefault(tenant_id, _TenantSchedule())
        for tenant_id in tuple(self._schedule):
            if tenant_id not in active:
                del self._schedule[tenant_id]

    def _select_ready_tenants(self, *, limit: int) -> tuple[str, ...]:
        if limit == 0:
            return ()
        now = monotonic()
        selected: list[str] = []
        for _ in range(len(self._queue)):
            tenant_id = self._queue.popleft()
            self._queue.append(tenant_id)
            schedule = self._schedule[tenant_id]
            if tenant_id not in self._active_tasks and schedule.next_poll_at <= now:
                selected.append(tenant_id)
                if len(selected) >= limit:
                    break
        return tuple(selected)

    async def _run_tenant(self, tenant_id: str) -> bool:
        claim_owner = f"{self._worker_instance_id}:{uuid4().hex}"
        with tenant_scope(tenant_id):
            async with asyncio.timeout(self._tenant_watchdog_seconds):
                return await self._runner.run_once(
                    tenant_id=tenant_id,
                    worker_id=claim_owner,
                    max_pages=self._quantum_pages,
                    max_seconds=self._quantum_seconds,
                )

    def _record_progress(self, tenant_id: str) -> None:
        schedule = self._schedule[tenant_id]
        schedule.idle_attempts = 0
        schedule.next_poll_at = 0.0

    def _record_idle(self, tenant_id: str) -> None:
        schedule = self._schedule[tenant_id]
        schedule.idle_attempts = min(schedule.idle_attempts + 1, 16)
        delay = min(
            self._idle_backoff_max_seconds,
            self._poll_seconds * (2 ** (schedule.idle_attempts - 1)),
        )
        schedule.next_poll_at = monotonic() + delay
