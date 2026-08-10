from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.tenant_context import global_knowledge_scope, tenant_scope
from app.domain.models import RealtimeEvent
from app.domain.ports import (
    GLOBAL_KNOWLEDGE_PARTITION,
    EventPublisherPort,
    PublishedWidgetCachePort,
)
from app.integrations.postgres.models import OutboxEventModel, TenantModel

_SAFE_PAYLOAD_KEYS = frozenset(
    {
        "response_kind",
        "risk_level",
        "handoff_id",
        "status",
        "priority",
        "queue_id",
        "ownership_mode",
        "assigned_agent_id",
        "site_id",
        "public_widget_id",
        "version_id",
        "new_version_id",
    }
)


class PostgreSQLOutboxDispatcher:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        publisher: EventPublisherPort,
        *,
        batch_size: int = 100,
        lease_seconds: int = 30,
        max_attempts: int = 10,
        published_widget_cache: PublishedWidgetCachePort | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._publisher = publisher
        self._batch_size = batch_size
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts
        self._published_widget_cache = published_widget_cache

    async def dispatch_once(self) -> int:
        tenant_ids = await self._tenant_ids()
        published = 0
        for tenant_id in tenant_ids:
            scope = (
                global_knowledge_scope()
                if tenant_id == GLOBAL_KNOWLEDGE_PARTITION
                else tenant_scope(tenant_id)
            )
            with scope:
                events = await self._claim(tenant_id)
                for event in events:
                    try:
                        await self._invalidate_published_widget(event)
                        await self._publisher.publish(_to_realtime_event(event))
                    except Exception as exception:  # noqa: BLE001
                        await self._mark_failed(tenant_id, event.event_id, str(exception))
                        structlog.get_logger("outbox").error(
                            "outbox.publish_failed",
                            event_id=event.event_id,
                            event_type=event.event_type,
                            attempt=event.attempts,
                            dead_letter=event.attempts >= self._max_attempts,
                        )
                    else:
                        await self._mark_published(tenant_id, event.event_id)
                        published += 1
        return published

    async def _invalidate_published_widget(self, event: OutboxEventModel) -> None:
        if self._published_widget_cache is None or event.event_type not in {
            "widget_config.published",
            "widget_config.rolled_back",
        }:
            return
        public_widget_id = str((event.payload or {}).get("public_widget_id", ""))
        if public_widget_id:
            await self._published_widget_cache.invalidate(public_widget_id=public_widget_id)

    async def _tenant_ids(self) -> tuple[str, ...]:
        async with self._session_factory() as session:
            values = await session.scalars(
                select(TenantModel.tenant_id).where(TenantModel.status == "active")
            )
        return (*tuple(str(value) for value in values), GLOBAL_KNOWLEDGE_PARTITION)

    async def _claim(self, tenant_id: str) -> list[OutboxEventModel]:
        now = datetime.now(UTC)
        async with self._session_factory.begin() as session:
            events = list(
                await session.scalars(
                    select(OutboxEventModel)
                    .where(
                        OutboxEventModel.tenant_id == tenant_id,
                        OutboxEventModel.available_at <= now,
                        or_(
                            OutboxEventModel.status.in_(("pending", "failed")),
                            (OutboxEventModel.status == "processing")
                            & (OutboxEventModel.lease_until < now),
                        ),
                    )
                    .order_by(OutboxEventModel.id)
                    .limit(self._batch_size)
                    .with_for_update(skip_locked=True)
                )
            )
            for event in events:
                event.status = "processing"
                event.attempts += 1
                event.lease_until = now + timedelta(seconds=self._lease_seconds)
        return events

    async def _mark_published(self, tenant_id: str, event_id: str) -> None:
        async with self._session_factory.begin() as session:
            event = await _get_event(session, tenant_id, event_id)
            event.status = "published"
            event.published_at = datetime.now(UTC)
            event.lease_until = None
            event.last_error = None

    async def _mark_failed(self, tenant_id: str, event_id: str, error: str) -> None:
        async with self._session_factory.begin() as session:
            event = await _get_event(session, tenant_id, event_id)
            event.status = "dead_letter" if event.attempts >= self._max_attempts else "failed"
            event.available_at = datetime.now(UTC) + timedelta(
                seconds=min(300, 2 ** min(event.attempts, 8))
            )
            event.lease_until = None
            event.last_error = error[:500]


async def _get_event(session: AsyncSession, tenant_id: str, event_id: str) -> OutboxEventModel:
    event = await session.scalar(
        select(OutboxEventModel).where(
            OutboxEventModel.tenant_id == tenant_id,
            OutboxEventModel.event_id == event_id,
        )
    )
    if event is None:
        raise RuntimeError("outbox event not found")
    return event


def _to_realtime_event(event: OutboxEventModel) -> RealtimeEvent:
    payload = {
        key: value for key, value in (event.payload or {}).items() if key in _SAFE_PAYLOAD_KEYS
    }
    if "response_kind" in payload:
        payload["kind"] = payload.pop("response_kind")
    return RealtimeEvent(
        event_id=event.event_id,
        tenant_id=event.tenant_id,
        event_type=event.event_type,
        resource_type=event.resource_type,
        resource_id=event.resource_id,
        payload=payload,
        occurred_at=event.created_at,
    )
