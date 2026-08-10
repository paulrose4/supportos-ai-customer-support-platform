import asyncio

from app.knowledge.web.worker_coordinator import WebSyncWorkerCoordinator


class Catalog:
    def __init__(self, tenant_ids: tuple[str, ...]) -> None:
        self.tenant_ids = tenant_ids

    async def list_active_tenant_ids(self) -> tuple[str, ...]:
        return self.tenant_ids


class FailingCatalog(Catalog):
    fail = False

    async def list_active_tenant_ids(self) -> tuple[str, ...]:
        if self.fail:
            raise ConnectionError("control plane unavailable")
        return await super().list_active_tenant_ids()


class Runner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.active = 0
        self.maximum_active = 0

    async def run_once(
        self,
        *,
        tenant_id: str,
        worker_id: str,
        max_pages: int | None = None,
        max_seconds: float | None = None,
    ) -> bool:
        self.calls.append((tenant_id, worker_id))
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        await asyncio.sleep(0)
        self.active -= 1
        return True


async def test_coordinator_discovers_new_tenant_and_limits_active_tenants() -> None:
    catalog = Catalog(("tenant-a", "tenant-b", "tenant-c"))
    runner = Runner()
    coordinator = WebSyncWorkerCoordinator(
        catalog=catalog,
        runner=runner,
        worker_instance_id="worker",
        max_active_tenants=2,
        quantum_pages=3,
        quantum_seconds=5,
        poll_seconds=0.1,
        discovery_seconds=0.001,
        idle_backoff_max_seconds=0.1,
    )

    assert await coordinator.run_once() is True
    assert len(runner.calls) == 2
    assert len({tenant_id for tenant_id, _ in runner.calls}) == 2
    assert all(":" in worker_id for _, worker_id in runner.calls)

    catalog.tenant_ids = ("tenant-a", "tenant-b", "tenant-c", "tenant-d")
    await asyncio.sleep(0.002)
    assert await coordinator.run_once() is True
    assert "tenant-c" in {tenant_id for tenant_id, _ in runner.calls}


async def test_coordinator_keeps_tenant_context_isolated() -> None:
    catalog = Catalog(("tenant-a", "tenant-b"))
    observed: list[tuple[str, str | None]] = []

    class ContextRunner(Runner):
        async def run_once(self, **kwargs):  # type: ignore[no-untyped-def]
            from app.application.tenant_context import current_tenant_id

            observed.append((kwargs["tenant_id"], current_tenant_id()))
            return await super().run_once(**kwargs)

    runner = ContextRunner()
    coordinator = WebSyncWorkerCoordinator(
        catalog=catalog,
        runner=runner,
        worker_instance_id="worker",
        max_active_tenants=2,
        quantum_pages=3,
        quantum_seconds=5,
        poll_seconds=0.1,
        idle_backoff_max_seconds=0.1,
    )

    await coordinator.run_once()
    assert sorted(observed) == [("tenant-a", "tenant-a"), ("tenant-b", "tenant-b")]


async def test_coordinator_reports_activity_and_fails_closed_on_catalog_errors() -> None:
    catalog = FailingCatalog(("tenant-a",))
    runner = Runner()
    coverage: list[tuple[str, ...]] = []
    activity: list[tuple[str, ...]] = []

    async def on_coverage(tenant_ids: tuple[str, ...]) -> None:
        coverage.append(tenant_ids)

    async def on_activity(tenant_ids: tuple[str, ...]) -> None:
        activity.append(tenant_ids)

    coordinator = WebSyncWorkerCoordinator(
        catalog=catalog,
        runner=runner,
        worker_instance_id="worker",
        max_active_tenants=1,
        quantum_pages=1,
        quantum_seconds=1,
        poll_seconds=0.01,
        discovery_seconds=0,
        idle_backoff_max_seconds=0.01,
        on_coverage=on_coverage,
        on_activity=on_activity,
    )

    assert await coordinator.run_once() is True
    assert activity == [("tenant-a",), ()]
    catalog.fail = True
    assert await coordinator.run_once() is False
    assert coverage[-1] == ()
    assert activity[-1] == ()


async def test_coordinator_watchdog_isolates_a_hung_tenant() -> None:
    catalog = Catalog(("tenant-hung", "tenant-ready"))
    canceled = asyncio.Event()

    class PartiallyHungRunner(Runner):
        async def run_once(self, **kwargs):  # type: ignore[no-untyped-def]
            self.calls.append((kwargs["tenant_id"], kwargs["worker_id"]))
            if kwargs["tenant_id"] == "tenant-ready":
                return True
            try:
                await asyncio.Future()
            finally:
                canceled.set()

    runner = PartiallyHungRunner()
    coordinator = WebSyncWorkerCoordinator(
        catalog=catalog,
        runner=runner,
        worker_instance_id="worker",
        max_active_tenants=2,
        quantum_pages=1,
        quantum_seconds=1,
        tenant_watchdog_seconds=1.01,
        poll_seconds=0.01,
        idle_backoff_max_seconds=0.01,
    )

    assert await coordinator.run_once() is True
    assert canceled.is_set()
    assert {tenant_id for tenant_id, _ in runner.calls} == {
        "tenant-hung",
        "tenant-ready",
    }


async def test_continuous_scheduler_rotates_while_another_tenant_is_still_running() -> None:
    catalog = Catalog(("tenant-long", "tenant-b", "tenant-c"))
    tenant_c_started = asyncio.Event()
    long_tenant_canceled = asyncio.Event()

    class LongAndShortRunner(Runner):
        async def run_once(self, **kwargs):  # type: ignore[no-untyped-def]
            tenant_id = kwargs["tenant_id"]
            self.calls.append((tenant_id, kwargs["worker_id"]))
            if tenant_id == "tenant-long":
                try:
                    await asyncio.Future()
                finally:
                    long_tenant_canceled.set()
            if tenant_id == "tenant-c":
                tenant_c_started.set()
            await asyncio.sleep(0)
            return True

    runner = LongAndShortRunner()
    coordinator = WebSyncWorkerCoordinator(
        catalog=catalog,
        runner=runner,
        worker_instance_id="worker",
        max_active_tenants=2,
        quantum_pages=1,
        quantum_seconds=1,
        tenant_watchdog_seconds=60,
        poll_seconds=0.001,
        idle_backoff_max_seconds=0.001,
    )
    scheduler = asyncio.create_task(coordinator.run_forever())
    try:
        await asyncio.wait_for(tenant_c_started.wait(), timeout=0.2)
        assert "tenant-long" in {tenant_id for tenant_id, _ in runner.calls}
        assert long_tenant_canceled.is_set() is False
    finally:
        scheduler.cancel()
        await asyncio.gather(scheduler, return_exceptions=True)

    assert long_tenant_canceled.is_set()
