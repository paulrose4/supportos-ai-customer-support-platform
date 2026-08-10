from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.models import HandoffRequest, HandoffStatus
from app.domain.rules import handoff_context_issues, redact_sensitive_text
from app.integrations.postgres.models import (
    AuditEventModel,
    ConversationModel,
    HandoffRequestModel,
)


class PostgreSQLQueueHandoffAdapter:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(self, request: HandoffRequest) -> HandoffRequest:
        if request.context_schema_version == 2:
            issues = handoff_context_issues(request)
            if issues:
                raise ValueError(f"handoff context is incomplete: {','.join(issues)}")
        try:
            async with self._session_factory.begin() as session:
                existing = await _find_existing(session, request.tenant_id, request.idempotency_key)
                if existing is not None:
                    return _to_domain(existing)

                created_at = datetime.now(UTC)
                session.add(
                    HandoffRequestModel(
                        tenant_id=request.tenant_id,
                        handoff_id=request.handoff_id,
                        customer_id=request.customer_id,
                        conversation_id=request.conversation_id,
                        reason_code=request.reason_code,
                        risk_level=request.risk_level,
                        summary=request.summary,
                        verified_evidence=list(request.verified_evidence),
                        failed_tools=list(request.failed_tools),
                        knowledge_sources=list(request.knowledge_sources),
                        user_intent=request.user_intent,
                        unresolved_question=request.unresolved_question,
                        ai_attempt=request.ai_attempt,
                        suggested_next_action=request.suggested_next_action,
                        reply_draft=request.reply_draft,
                        context_schema_version=request.context_schema_version,
                        customer_language=request.customer_language,
                        customer_region=request.customer_region,
                        identity_status=request.identity_status,
                        product_ids=list(request.product_ids),
                        order_id=request.order_id,
                        confirmed_fields=list(request.confirmed_fields),
                        customer_sentiment=request.customer_sentiment,
                        customer_request=request.customer_request,
                        commitment_deadline=request.commitment_deadline,
                        queue_id=request.queue_id,
                        status=request.status.value,
                        idempotency_key=request.idempotency_key,
                        trace_id=request.trace_id,
                        sla_policy_version=request.sla_policy_version,
                        expected_response_at=request.expected_response_at,
                        priority=request.priority,
                        assigned_agent_id=request.assigned_agent_id,
                        accepted_at=request.accepted_at,
                        resolved_at=request.resolved_at,
                        created_at=created_at,
                        updated_at=created_at,
                    )
                )
                session.add(
                    AuditEventModel(
                        tenant_id=request.tenant_id,
                        event_id=str(uuid4()),
                        event_type="handoff.created",
                        actor_subject_id=request.customer_id,
                        trace_id=request.trace_id,
                        resource_type="handoff_request",
                        resource_id=request.handoff_id,
                        details={
                            "reason_code": request.reason_code,
                            "risk_level": request.risk_level,
                            "status": request.status.value,
                        },
                        created_at=created_at,
                    )
                )
            return replace(request, created_at=created_at)
        except IntegrityError:
            async with self._session_factory() as session:
                existing = await _find_existing(session, request.tenant_id, request.idempotency_key)
                if existing is None:
                    raise
                return _to_domain(existing)

    async def list_for_tenant(
        self,
        *,
        tenant_id: str,
        status: str | None,
        limit: int,
    ) -> list[HandoffRequest]:
        items, _ = await self.list_page_for_tenant(
            tenant_id=tenant_id,
            status=status,
            limit=limit,
            cursor=None,
            site_id=None,
            assigned_agent_id=None,
        )
        return items

    async def list_page_for_tenant(
        self,
        *,
        tenant_id: str,
        status: str | None,
        limit: int,
        cursor: str | None = None,
        site_id: str | None = None,
        assigned_agent_id: str | None = None,
    ) -> tuple[list[HandoffRequest], str | None]:
        statement = (
            select(HandoffRequestModel)
            .where(HandoffRequestModel.tenant_id == tenant_id)
            .order_by(HandoffRequestModel.created_at.desc(), HandoffRequestModel.handoff_id.desc())
            .limit(limit + 1)
        )
        if status is not None:
            statement = statement.where(HandoffRequestModel.status == status)
        if assigned_agent_id is not None:
            statement = statement.where(HandoffRequestModel.assigned_agent_id == assigned_agent_id)
        if site_id is not None:
            statement = statement.join(
                ConversationModel,
                and_(
                    ConversationModel.tenant_id == HandoffRequestModel.tenant_id,
                    ConversationModel.conversation_id == HandoffRequestModel.conversation_id,
                ),
            ).where(ConversationModel.site_id == site_id)
        if cursor is not None:
            cursor_time, cursor_id = _decode_cursor(cursor)
            statement = statement.where(
                or_(
                    HandoffRequestModel.created_at < cursor_time,
                    and_(
                        HandoffRequestModel.created_at == cursor_time,
                        HandoffRequestModel.handoff_id < cursor_id,
                    ),
                )
            )
        async with self._session_factory() as session:
            models = list(await session.scalars(statement))
        has_more = len(models) > limit
        models = models[:limit]
        next_cursor = _encode_cursor(models[-1]) if has_more and models else None
        return [_to_domain(model) for model in models], next_cursor


async def _find_existing(
    session: AsyncSession, tenant_id: str, idempotency_key: str
) -> HandoffRequestModel | None:
    statement = select(HandoffRequestModel).where(
        HandoffRequestModel.tenant_id == tenant_id,
        HandoffRequestModel.idempotency_key == idempotency_key,
    )
    return await session.scalar(statement)


def _to_domain(model: HandoffRequestModel) -> HandoffRequest:
    return HandoffRequest(
        handoff_id=model.handoff_id,
        tenant_id=model.tenant_id,
        customer_id=model.customer_id,
        conversation_id=model.conversation_id,
        reason_code=model.reason_code,
        risk_level=model.risk_level,
        summary=redact_sensitive_text(model.summary),
        verified_evidence=tuple(model.verified_evidence),
        failed_tools=tuple(model.failed_tools),
        knowledge_sources=tuple(model.knowledge_sources),
        created_at=model.created_at,
        status=HandoffStatus(model.status),
        idempotency_key=model.idempotency_key,
        trace_id=model.trace_id,
        sla_policy_version=model.sla_policy_version,
        expected_response_at=model.expected_response_at,
        priority=model.priority,
        assigned_agent_id=model.assigned_agent_id,
        accepted_at=model.accepted_at,
        resolved_at=model.resolved_at,
        user_intent=model.user_intent,
        unresolved_question=redact_sensitive_text(model.unresolved_question or "") or None,
        ai_attempt=redact_sensitive_text(model.ai_attempt or "") or None,
        suggested_next_action=model.suggested_next_action,
        reply_draft=redact_sensitive_text(model.reply_draft or "") or None,
        queue_id=model.queue_id,
        context_schema_version=model.context_schema_version,
        customer_language=model.customer_language,
        customer_region=model.customer_region,
        identity_status=model.identity_status,
        product_ids=tuple(model.product_ids or []),
        order_id=model.order_id,
        confirmed_fields=tuple(model.confirmed_fields or []),
        customer_sentiment=model.customer_sentiment,
        customer_request=redact_sensitive_text(model.customer_request or "") or None,
        commitment_deadline=model.commitment_deadline,
    )


def _encode_cursor(model: HandoffRequestModel) -> str:
    return f"{model.created_at.isoformat()}|{model.handoff_id}"


def _decode_cursor(value: str) -> tuple[datetime, str]:
    try:
        raw_time, handoff_id = value.rsplit("|", 1)
        created_at = datetime.fromisoformat(raw_time)
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid handoff cursor") from exc
    if not handoff_id or created_at.tzinfo is None:
        raise ValueError("invalid handoff cursor")
    return created_at, handoff_id
