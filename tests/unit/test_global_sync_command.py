import argparse
from types import SimpleNamespace

import pytest

from app.application.tenant_context import global_knowledge_access_enabled
from app.config import Settings
from scripts import sync_global_knowledge


def _args(**overrides):  # type: ignore[no-untyped-def]
    values = {
        "confirm_global_sync": True,
        "approval_reference": "review-2026-001",
        "actor_subject_id": "platform-operator",
        "correlation_id": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


@pytest.mark.asyncio
async def test_global_sync_command_is_disabled_by_default(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(sync_global_knowledge, "get_settings", lambda: Settings(_env_file=None))

    with pytest.raises(PermissionError, match="disabled"):
        await sync_global_knowledge._run(_args())


@pytest.mark.asyncio
async def test_global_sync_command_requires_explicit_confirmation(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    settings = Settings(_env_file=None, platform_global_sync_enabled=True)
    monkeypatch.setattr(sync_global_knowledge, "get_settings", lambda: settings)

    with pytest.raises(PermissionError, match="confirm"):
        await sync_global_knowledge._run(_args(confirm_global_sync=False))


def test_tenant_owner_does_not_receive_global_sync_scope() -> None:
    from app.domain.rules.rbac import scopes_for_roles

    assert "knowledge:sync:global" not in scopes_for_roles(frozenset({"tenant_owner"}))


@pytest.mark.asyncio
async def test_global_sync_command_sets_global_database_scope(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    settings = Settings(_env_file=None, platform_global_sync_enabled=True)

    class Adapter:
        async def initialize(self) -> None:
            pass

        async def close(self) -> None:
            pass

    class Database:
        async def dispose(self) -> None:
            pass

    class Service:
        async def sync(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            assert global_knowledge_access_enabled() is True
            return SimpleNamespace(
                sync_job_id="job-1",
                discovered_count=1,
                indexed_count=1,
                skipped_count=0,
                failed_count=0,
                errors={},
            )

    container = SimpleNamespace(
        knowledge_adapter=Adapter(),
        global_knowledge_sync_service=Service(),
        database=Database(),
    )
    monkeypatch.setattr(sync_global_knowledge, "get_settings", lambda: settings)

    async def build(_settings):  # type: ignore[no-untyped-def]
        return container

    monkeypatch.setattr(sync_global_knowledge, "build_container", build)

    assert await sync_global_knowledge._run(_args()) == 0
