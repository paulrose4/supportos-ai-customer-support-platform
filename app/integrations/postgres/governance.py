from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.models.governance import AuditEvent
from app.integrations.postgres.models import AuditEventModel


class PostgreSQLAuditLogAdapter:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list_events(
        self,
        *,
        tenant_id: str,
        event_type: str | None,
        actor_subject_id: str | None,
        resource_type: str | None,
        correlation_id: str | None,
        created_from,
        created_to,
        cursor_created_at,
        cursor_event_id: str | None,
        limit: int,
    ) -> list[AuditEvent]:
        statement = select(AuditEventModel).where(AuditEventModel.tenant_id == tenant_id)
        if event_type is not None:
            statement = statement.where(AuditEventModel.event_type == event_type)
        if actor_subject_id is not None:
            statement = statement.where(AuditEventModel.actor_subject_id == actor_subject_id)
        if resource_type is not None:
            statement = statement.where(AuditEventModel.resource_type == resource_type)
        if correlation_id is not None:
            statement = statement.where(AuditEventModel.correlation_id == correlation_id)
        if created_from is not None:
            statement = statement.where(AuditEventModel.created_at >= created_from)
        if created_to is not None:
            statement = statement.where(AuditEventModel.created_at <= created_to)
        if cursor_created_at is not None and cursor_event_id is not None:
            statement = statement.where(
                or_(
                    AuditEventModel.created_at < cursor_created_at,
                    and_(
                        AuditEventModel.created_at == cursor_created_at,
                        AuditEventModel.event_id < cursor_event_id,
                    ),
                )
            )
        statement = statement.order_by(
            AuditEventModel.created_at.desc(), AuditEventModel.event_id.desc()
        ).limit(limit)
        async with self._session_factory() as session:
            models = list(await session.scalars(statement))
        return [_to_domain(model) for model in models]


def _to_domain(model: AuditEventModel) -> AuditEvent:
    return AuditEvent(
        event_id=model.event_id,
        tenant_id=model.tenant_id,
        event_type=model.event_type,
        actor_subject_id=model.actor_subject_id,
        correlation_id=model.correlation_id,
        trace_id=model.trace_id,
        resource_type=model.resource_type,
        resource_id=model.resource_id,
        details=dict(model.details or {}),
        created_at=model.created_at,
    )
