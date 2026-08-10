import asyncio

from app.application.dto import ReadinessResult
from scripts.run_web_sync_worker import (
    _initialize_knowledge_adapter,
    _write_functional_heartbeat,
)


async def test_worker_retries_qdrant_initialization_until_it_recovers() -> None:
    attempts = 0
    delays: list[float] = []

    async def initialize() -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ConnectionError("qdrant is starting")

    async def sleep(delay: float) -> None:
        delays.append(delay)

    await _initialize_knowledge_adapter(
        initialize,
        max_delay_seconds=30,
        sleep=sleep,
        jitter=lambda _low, _high: 1.0,
    )

    assert attempts == 3
    assert delays == [1.0, 2.0]


async def test_worker_heartbeat_advances_only_after_dependencies_are_ready(tmp_path) -> None:
    heartbeat = tmp_path / "worker.heartbeat"
    checks = 0

    async def readiness() -> ReadinessResult:
        nonlocal checks
        checks += 1
        if checks == 1:
            return ReadinessResult(False, ("qdrant",))
        return ReadinessResult(True)

    task = asyncio.create_task(
        _write_functional_heartbeat(
            heartbeat,
            readiness,
            interval_seconds=0.01,
        )
    )
    try:
        for _attempt in range(50):
            if heartbeat.exists():
                break
            await asyncio.sleep(0.01)
        assert heartbeat.exists()
        assert checks >= 2
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
