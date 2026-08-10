from datetime import datetime
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.models.retention import RetentionCounts
from app.integrations.postgres.models import (
    AdminSessionModel,
    AuditEventModel,
    CustomerMemoryItemModel,
    MemoryCandidateModel,
    ResolutionEpisodeModel,
    SupportOperationRequestModel,
)


class PostgreSQLRetentionAdapter:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def preview(
        self,
        *,
        tenant_id: str,
        session_cutoff: datetime,
        memory_cutoff: datetime,
        operation_cutoff: datetime,
        audit_cutoff: datetime,
    ) -> RetentionCounts:
        async with self._session_factory() as session:
            return RetentionCounts(
                admin_sessions=await _count(
                    session, AdminSessionModel, _session_filter(tenant_id, session_cutoff)
                ),
                expired_customer_memory=await _count(
                    session,
                    CustomerMemoryItemModel,
                    _memory_filter(tenant_id, memory_cutoff),
                ),
                expired_memory_candidates=await _count(
                    session, MemoryCandidateModel, _candidate_filter(tenant_id, memory_cutoff)
                ),
                expired_resolution_episodes=await _count(
                    session, ResolutionEpisodeModel, _episode_filter(tenant_id, memory_cutoff)
                ),
                support_operation_requests=await _count(
                    session,
                    SupportOperationRequestModel,
                    _operation_filter(tenant_id, operation_cutoff),
                ),
                audit_events=await _count(
                    session, AuditEventModel, _audit_filter(tenant_id, audit_cutoff)
                ),
            )

    async def execute(
        self,
        *,
        tenant_id: str,
        session_cutoff,
        memory_cutoff,
        operation_cutoff,
        audit_cutoff,
        run_key: str,
        policy_version: str,
        approval_reference: str,
        actor_subject_id: str,
        correlation_id: str,
        executed_at: datetime,
    ) -> RetentionCounts:
        event_id = str(uuid5(NAMESPACE_URL, f"retention:{tenant_id}:{run_key}"))
        async with self._session_factory.begin() as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
                {"lock_key": f"retention:{tenant_id}:{run_key}"},
            )
            existing = await session.scalar(
                select(AuditEventModel).where(
                    AuditEventModel.tenant_id == tenant_id,
                    AuditEventModel.event_id == event_id,
                )
            )
            if existing is not None:
                return _counts_from_details(existing.details)

            sessions = await session.execute(
                delete(AdminSessionModel).where(*_session_filter(tenant_id, session_cutoff))
            )
            memories = await session.execute(
                delete(CustomerMemoryItemModel).where(*_memory_filter(tenant_id, memory_cutoff))
            )
            candidates = await session.execute(
                delete(MemoryCandidateModel).where(*_candidate_filter(tenant_id, memory_cutoff))
            )
            episodes = await session.execute(
                delete(ResolutionEpisodeModel).where(*_episode_filter(tenant_id, memory_cutoff))
            )
            operations = await session.execute(
                delete(SupportOperationRequestModel).where(
                    *_operation_filter(tenant_id, operation_cutoff)
                )
            )
            audits = await session.execute(
                delete(AuditEventModel).where(*_audit_filter(tenant_id, audit_cutoff))
            )
            counts = RetentionCounts(
                admin_sessions=sessions.rowcount or 0,
                expired_customer_memory=memories.rowcount or 0,
                expired_memory_candidates=candidates.rowcount or 0,
                expired_resolution_episodes=episodes.rowcount or 0,
                support_operation_requests=operations.rowcount or 0,
                audit_events=audits.rowcount or 0,
            )
            session.add(
                AuditEventModel(
                    tenant_id=tenant_id,
                    event_id=event_id,
                    event_type="retention.executed",
                    actor_subject_id=actor_subject_id,
                    correlation_id=correlation_id,
                    resource_type="tenant",
                    resource_id=tenant_id,
                    details={
                        "run_key": run_key,
                        "policy_version": policy_version,
                        "approval_reference": approval_reference,
                        "counts": {
                            "admin_sessions": counts.admin_sessions,
                            "expired_customer_memory": counts.expired_customer_memory,
                            "expired_memory_candidates": counts.expired_memory_candidates,
                            "expired_resolution_episodes": counts.expired_resolution_episodes,
                            "support_operation_requests": counts.support_operation_requests,
                            "audit_events": counts.audit_events,
                        },
                    },
                    created_at=executed_at,
                )
            )
            return counts


def _session_filter(tenant_id, cutoff):  # type: ignore[no-untyped-def]
    return (
        AdminSessionModel.tenant_id == tenant_id,
        or_(
            AdminSessionModel.revoked_at < cutoff,
            AdminSessionModel.expires_at < cutoff,
        ),
    )


def _memory_filter(tenant_id, cutoff):  # type: ignore[no-untyped-def]
    return (
        CustomerMemoryItemModel.tenant_id == tenant_id,
        CustomerMemoryItemModel.expires_at.is_not(None),
        CustomerMemoryItemModel.expires_at < cutoff,
    )


def _operation_filter(tenant_id, cutoff):  # type: ignore[no-untyped-def]
    return (
        SupportOperationRequestModel.tenant_id == tenant_id,
        SupportOperationRequestModel.created_at < cutoff,
    )


def _candidate_filter(tenant_id, cutoff):  # type: ignore[no-untyped-def]
    return (
        MemoryCandidateModel.tenant_id == tenant_id,
        MemoryCandidateModel.expires_at < cutoff,
    )


def _episode_filter(tenant_id, cutoff):  # type: ignore[no-untyped-def]
    return (
        ResolutionEpisodeModel.tenant_id == tenant_id,
        ResolutionEpisodeModel.expires_at < cutoff,
    )


def _audit_filter(tenant_id, cutoff):  # type: ignore[no-untyped-def]
    return (
        AuditEventModel.tenant_id == tenant_id,
        AuditEventModel.created_at < cutoff,
        AuditEventModel.event_type != "retention.executed",
    )


async def _count(session: AsyncSession, model: type, filters: tuple) -> int:
    value = await session.scalar(select(func.count()).select_from(model).where(*filters))
    return int(value or 0)


def _counts_from_details(details: dict) -> RetentionCounts:
    values = details.get("counts", {}) if isinstance(details, dict) else {}
    return RetentionCounts(
        admin_sessions=int(values.get("admin_sessions", 0)),
        expired_customer_memory=int(values.get("expired_customer_memory", 0)),
        expired_memory_candidates=int(values.get("expired_memory_candidates", 0)),
        expired_resolution_episodes=int(values.get("expired_resolution_episodes", 0)),
        support_operation_requests=int(values.get("support_operation_requests", 0)),
        audit_events=int(values.get("audit_events", 0)),
    )
