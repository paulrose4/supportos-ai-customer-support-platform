import base64
import json
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import cast, exists, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.models import (
    CannedReply,
    ConversationPriority,
    ConversationStatus,
    ConversationWorkspace,
    CustomerDirectoryItem,
    CustomerMemoryItem,
    InboxConversation,
    InboxCounts,
    MemoryCandidate,
    MemoryCandidateStatus,
    MemoryKind,
    OwnershipMode,
    ResolutionEpisode,
    SupportAgentOption,
    SupportMessage,
    SupportQueue,
    SupportQueueMember,
    SupportSite,
)
from app.domain.rules import (
    classify_conversation_intent,
    extract_product_skus,
    redact_sensitive_text,
)
from app.integrations.postgres.experience import (
    record_human_message_experience,
    record_manual_resolution_experience,
)
from app.integrations.postgres.models import (
    AuditEventModel,
    AutomationRuleModel,
    CannedReplyModel,
    ConversationModel,
    CustomerMemoryItemModel,
    CustomerModel,
    HandoffRequestModel,
    IdentityUserModel,
    MemoryCandidateModel,
    MessageModel,
    ResolutionEpisodeModel,
    SupportOperationRequestModel,
    SupportQueueMembershipModel,
    SupportQueueModel,
    SupportSiteModel,
    TenantMembershipModel,
)


class PostgreSQLSupportOperationsAdapter:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list_sites(self, *, tenant_id: str) -> list[SupportSite]:
        async with self._session_factory() as session:
            models = list(
                await session.scalars(
                    select(SupportSiteModel)
                    .where(SupportSiteModel.tenant_id == tenant_id)
                    .order_by(SupportSiteModel.name.asc())
                )
            )
        return [_site_to_domain(model) for model in models]

    async def inbox_counts(
        self, *, tenant_id: str, site_id: str | None, assigned_agent_id: str
    ) -> InboxCounts:
        now = datetime.now(UTC)
        filters = [ConversationModel.tenant_id == tenant_id]
        if site_id is not None:
            filters.append(ConversationModel.site_id == site_id)
        sla_exists = exists(
            select(HandoffRequestModel.id).where(
                HandoffRequestModel.tenant_id == ConversationModel.tenant_id,
                HandoffRequestModel.conversation_id == ConversationModel.conversation_id,
                HandoffRequestModel.status.in_(("pending", "assigned")),
                or_(
                    HandoffRequestModel.commitment_deadline <= now,
                    HandoffRequestModel.expected_response_at <= now,
                ),
            )
        )
        statement = select(
            func.count(ConversationModel.id),
            func.count(ConversationModel.id).filter(
                ConversationModel.assigned_agent_id == assigned_agent_id
            ),
            func.count(ConversationModel.id).filter(
                (ConversationModel.status == ConversationStatus.WAITING_HUMAN.value)
                & (ConversationModel.ownership_mode == OwnershipMode.QUEUED.value)
            ),
            func.count(ConversationModel.id).filter(sla_exists),
            func.count(ConversationModel.id).filter(ConversationModel.unread_count > 0),
            func.count(ConversationModel.id).filter(
                or_(
                    ConversationModel.risk_level >= 2,
                    ConversationModel.priority.in_(("high", "urgent")),
                )
            ),
            func.count(ConversationModel.id).filter(
                ConversationModel.status == ConversationStatus.RESOLVED.value
            ),
        ).where(*filters)
        async with self._session_factory() as session:
            row = (await session.execute(statement)).one()
        return InboxCounts(*(int(value or 0) for value in row))

    async def list_inbox(
        self,
        *,
        tenant_id: str,
        status: ConversationStatus | None,
        ownership_mode: OwnershipMode | None,
        assigned_agent_id: str | None,
        site_id: str | None,
        queue_id: str | None,
        priority: ConversationPriority | None,
        tag: str | None,
        unread_only: bool,
        customer_id: str | None,
        limit: int,
        cursor: str | None = None,
        search: str | None = None,
        sla_risk_only: bool = False,
        priority_risk_only: bool = False,
    ) -> tuple[list[InboxConversation], str | None, int]:
        last_message = (
            select(MessageModel.content)
            .where(
                MessageModel.tenant_id == ConversationModel.tenant_id,
                MessageModel.conversation_id == ConversationModel.conversation_id,
                MessageModel.message_type == "chat",
            )
            .order_by(MessageModel.created_at.desc(), MessageModel.id.desc())
            .limit(1)
            .correlate(ConversationModel)
            .scalar_subquery()
        )
        latest_handoff_due = (
            select(
                func.coalesce(
                    HandoffRequestModel.commitment_deadline,
                    HandoffRequestModel.expected_response_at,
                )
            )
            .where(
                HandoffRequestModel.tenant_id == ConversationModel.tenant_id,
                HandoffRequestModel.conversation_id == ConversationModel.conversation_id,
                HandoffRequestModel.status.in_(("pending", "assigned")),
            )
            .order_by(HandoffRequestModel.created_at.desc())
            .limit(1)
            .correlate(ConversationModel)
            .scalar_subquery()
        )
        latest_handoff_reason = (
            select(HandoffRequestModel.reason_code)
            .where(
                HandoffRequestModel.tenant_id == ConversationModel.tenant_id,
                HandoffRequestModel.conversation_id == ConversationModel.conversation_id,
                HandoffRequestModel.status.in_(("pending", "assigned")),
            )
            .order_by(HandoffRequestModel.created_at.desc())
            .limit(1)
            .correlate(ConversationModel)
            .scalar_subquery()
        )
        statement = (
            select(
                ConversationModel,
                CustomerModel.display_name,
                last_message,
                latest_handoff_due,
                latest_handoff_reason,
            )
            .outerjoin(
                CustomerModel,
                (CustomerModel.tenant_id == ConversationModel.tenant_id)
                & (CustomerModel.customer_id == ConversationModel.customer_id),
            )
            .where(ConversationModel.tenant_id == tenant_id)
            .order_by(ConversationModel.updated_at.desc(), ConversationModel.id.desc())
        )
        if status is not None:
            statement = statement.where(ConversationModel.status == status.value)
        if ownership_mode is not None:
            statement = statement.where(ConversationModel.ownership_mode == ownership_mode.value)
        if assigned_agent_id is not None:
            statement = statement.where(ConversationModel.assigned_agent_id == assigned_agent_id)
        if site_id is not None:
            statement = statement.where(ConversationModel.site_id == site_id)
        if queue_id is not None:
            statement = statement.where(ConversationModel.queue_id == queue_id)
        if priority is not None:
            statement = statement.where(ConversationModel.priority == priority.value)
        if tag is not None:
            statement = statement.where(cast(ConversationModel.tags, JSONB).contains([tag]))
        if unread_only:
            statement = statement.where(ConversationModel.unread_count > 0)
        if customer_id is not None:
            statement = statement.where(ConversationModel.customer_id == customer_id)
        if search:
            term = f"%{search.strip()}%"
            message_match = exists(
                select(MessageModel.id).where(
                    MessageModel.tenant_id == ConversationModel.tenant_id,
                    MessageModel.conversation_id == ConversationModel.conversation_id,
                    MessageModel.content.ilike(term),
                )
            )
            statement = statement.where(
                or_(
                    ConversationModel.customer_id.ilike(term),
                    ConversationModel.conversation_id.ilike(term),
                    CustomerModel.display_name.ilike(term),
                    message_match,
                )
            )
        if priority_risk_only:
            statement = statement.where(
                or_(
                    ConversationModel.risk_level >= 2,
                    ConversationModel.priority.in_(("high", "urgent")),
                )
            )
        if sla_risk_only:
            statement = statement.where(
                exists(
                    select(HandoffRequestModel.id).where(
                        HandoffRequestModel.tenant_id == ConversationModel.tenant_id,
                        HandoffRequestModel.conversation_id == ConversationModel.conversation_id,
                        HandoffRequestModel.status.in_(("pending", "assigned")),
                        or_(
                            HandoffRequestModel.commitment_deadline <= datetime.now(UTC),
                            HandoffRequestModel.expected_response_at <= datetime.now(UTC),
                        ),
                    )
                )
            )
        total_statement = statement.with_only_columns(
            func.count(ConversationModel.id), maintain_column_froms=True
        ).order_by(None)
        if cursor:
            cursor_updated_at_raw, cursor_id = _decode_page_cursor(cursor, "inbox")
            cursor_updated_at = datetime.fromisoformat(cursor_updated_at_raw)
            statement = statement.where(
                (ConversationModel.updated_at < cursor_updated_at)
                | (
                    (ConversationModel.updated_at == cursor_updated_at)
                    & (ConversationModel.id < int(cursor_id))
                )
            )
        statement = statement.limit(limit + 1)
        async with self._session_factory() as session:
            rows = list((await session.execute(statement)).all())
            total = int(await session.scalar(total_statement) or 0)
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = [
            _conversation_to_domain(model, display_name, preview, sla_due_at, handoff_reason)
            for model, display_name, preview, sla_due_at, handoff_reason in rows
        ]
        next_cursor = (
            _encode_page_cursor("inbox", rows[-1][0].updated_at, rows[-1][0].id)
            if has_more and rows
            else None
        )
        return items, next_cursor, total

    async def list_customers(
        self,
        *,
        tenant_id: str,
        site_id: str | None,
        search: str | None,
        limit: int,
        cursor: str | None = None,
    ) -> tuple[list[CustomerDirectoryItem], str | None, int]:
        conversation_count = func.count(ConversationModel.id)
        last_conversation_at = func.max(ConversationModel.updated_at)
        statement = (
            select(
                CustomerModel.customer_id,
                CustomerModel.display_name,
                conversation_count,
                last_conversation_at,
            )
            .outerjoin(
                ConversationModel,
                (ConversationModel.tenant_id == CustomerModel.tenant_id)
                & (ConversationModel.customer_id == CustomerModel.customer_id),
            )
            .where(CustomerModel.tenant_id == tenant_id)
            .group_by(
                CustomerModel.customer_id,
                CustomerModel.display_name,
                CustomerModel.created_at,
            )
            .order_by(last_conversation_at.desc().nullslast(), CustomerModel.customer_id.desc())
        )
        if site_id is not None:
            statement = statement.where(ConversationModel.site_id == site_id)
        normalized_search = (search or "").strip()
        if normalized_search:
            pattern = f"%{normalized_search}%"
            statement = statement.where(
                CustomerModel.customer_id.ilike(pattern) | CustomerModel.display_name.ilike(pattern)
            )
        total_statement = (
            select(func.count(func.distinct(CustomerModel.customer_id)))
            .select_from(CustomerModel)
            .outerjoin(
                ConversationModel,
                (ConversationModel.tenant_id == CustomerModel.tenant_id)
                & (ConversationModel.customer_id == CustomerModel.customer_id),
            )
            .where(CustomerModel.tenant_id == tenant_id)
        )
        if site_id is not None:
            total_statement = total_statement.where(ConversationModel.site_id == site_id)
        if normalized_search:
            total_statement = total_statement.where(
                CustomerModel.customer_id.ilike(pattern) | CustomerModel.display_name.ilike(pattern)
            )
        if cursor:
            cursor_last_seen, cursor_customer_id = _decode_page_cursor(cursor, "customers")
            if cursor_last_seen == "null":
                statement = statement.having(CustomerModel.customer_id < cursor_customer_id)
            else:
                last_seen = datetime.fromisoformat(cursor_last_seen)
                statement = statement.having(
                    (last_conversation_at < last_seen)
                    | (
                        (last_conversation_at == last_seen)
                        & (CustomerModel.customer_id < cursor_customer_id)
                    )
                )
        statement = statement.limit(limit + 1)
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).all()
            total = int(await session.scalar(total_statement) or 0)
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = [
            CustomerDirectoryItem(
                customer_id=customer_id,
                tenant_id=tenant_id,
                display_name=display_name,
                conversation_count=int(count),
                last_conversation_at=last_seen,
            )
            for customer_id, display_name, count, last_seen in rows
        ]
        next_cursor = None
        if has_more and rows:
            last_customer_id = rows[-1][0]
            last_seen = rows[-1][3]
            next_cursor = _encode_page_cursor("customers", last_seen, last_customer_id)
        return items, next_cursor, total

    async def get_workspace(
        self, *, tenant_id: str, conversation_id: str
    ) -> ConversationWorkspace | None:
        async with self._session_factory() as session:
            return await _load_workspace(session, tenant_id, conversation_id)

    async def transition_conversation(
        self,
        *,
        tenant_id: str,
        conversation_id: str,
        actor_subject_id: str,
        correlation_id: str,
        target_status: ConversationStatus,
        target_ownership: OwnershipMode,
        assigned_agent_id: str | None,
        event_type: str,
        idempotency_key: str,
        expected_routing_version: int | None = None,
        force: bool = False,
    ) -> ConversationWorkspace:
        async with self._session_factory.begin() as session:
            existing = await _get_operation(session, tenant_id, idempotency_key)
            if existing is not None:
                _assert_same_operation(existing, event_type, conversation_id)
                workspace = await _load_workspace(session, tenant_id, conversation_id)
                if workspace is None:
                    raise LookupError("conversation not found")
                return workspace

            conversation = await session.scalar(
                select(ConversationModel)
                .where(
                    ConversationModel.tenant_id == tenant_id,
                    ConversationModel.conversation_id == conversation_id,
                )
                .with_for_update()
            )
            if conversation is None:
                raise LookupError("conversation not found")
            if (
                expected_routing_version is not None
                and conversation.routing_version != expected_routing_version
            ):
                raise RuntimeError("conversation changed; refresh before retrying")
            if event_type == "conversation.taken_over" and not force:
                if (
                    conversation.assigned_agent_id is not None
                    and conversation.assigned_agent_id != actor_subject_id
                ):
                    raise RuntimeError("conversation is assigned to another agent")
            if event_type in {"conversation.resolved", "conversation.released_to_ai"} and not force:
                if conversation.assigned_agent_id != actor_subject_id:
                    raise RuntimeError("only the assigned agent can change this conversation")
            concurrent = await _get_operation(session, tenant_id, idempotency_key)
            if concurrent is not None:
                _assert_same_operation(concurrent, event_type, conversation_id)
                workspace = await _load_workspace(session, tenant_id, conversation_id)
                if workspace is None:
                    raise LookupError("conversation not found")
                return workspace
            now = datetime.now(UTC)
            conversation.status = target_status.value
            conversation.ownership_mode = target_ownership.value
            conversation.assigned_agent_id = assigned_agent_id
            conversation.routing_version += 1
            conversation.unread_count = 0
            conversation.updated_at = now
            if event_type == "conversation.read":
                conversation.last_read_at = now

            if event_type == "conversation.taken_over":
                handoff = await session.scalar(
                    select(HandoffRequestModel)
                    .where(
                        HandoffRequestModel.tenant_id == tenant_id,
                        HandoffRequestModel.conversation_id == conversation_id,
                        HandoffRequestModel.status == "pending",
                    )
                    .order_by(HandoffRequestModel.created_at.asc())
                    .with_for_update()
                )
                if handoff is not None:
                    handoff.status = "assigned"
                    handoff.assigned_agent_id = actor_subject_id
                    handoff.accepted_at = now
                    handoff.updated_at = now
            elif event_type == "conversation.resolved":
                conversation.resolved_at = now
                handoffs = list(
                    await session.scalars(
                        select(HandoffRequestModel)
                        .where(
                            HandoffRequestModel.tenant_id == tenant_id,
                            HandoffRequestModel.conversation_id == conversation_id,
                            HandoffRequestModel.status.in_(("pending", "assigned")),
                        )
                        .with_for_update()
                    )
                )
                for handoff in handoffs:
                    handoff.status = "resolved"
                    handoff.assigned_agent_id = actor_subject_id
                    handoff.resolved_at = now
                    handoff.updated_at = now
                await _create_manual_resolution_episode(
                    session=session,
                    conversation=conversation,
                    resolved_at=now,
                    actor_subject_id=actor_subject_id,
                )
                await record_manual_resolution_experience(
                    session,
                    tenant_id=tenant_id,
                    site_id=conversation.site_id or "default-site",
                    conversation_id=conversation_id,
                    actor_subject_id=actor_subject_id,
                    occurred_at=now,
                )

            _record_write(
                session=session,
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                operation=event_type,
                resource_type="conversation",
                resource_id=conversation_id,
                actor_subject_id=actor_subject_id,
                correlation_id=correlation_id,
                details={
                    "status": target_status.value,
                    "ownership_mode": target_ownership.value,
                    "assigned_agent_id": assigned_agent_id,
                },
                created_at=now,
            )
            await session.flush()
            workspace = await _load_workspace(session, tenant_id, conversation_id)
            if workspace is None:
                raise LookupError("conversation not found")
            return workspace

    async def append_agent_message(
        self,
        *,
        tenant_id: str,
        conversation_id: str,
        actor_subject_id: str,
        correlation_id: str,
        content: str,
        idempotency_key: str,
    ) -> ConversationWorkspace:
        operation = "conversation.agent_message_sent"
        async with self._session_factory.begin() as session:
            existing = await _get_operation(session, tenant_id, idempotency_key)
            if existing is not None:
                _assert_same_operation(existing, operation, conversation_id)
                workspace = await _load_workspace(session, tenant_id, conversation_id)
                if workspace is None:
                    raise LookupError("conversation not found")
                return workspace
            conversation = await session.scalar(
                select(ConversationModel)
                .where(
                    ConversationModel.tenant_id == tenant_id,
                    ConversationModel.conversation_id == conversation_id,
                )
                .with_for_update()
            )
            if conversation is None:
                raise LookupError("conversation not found")
            concurrent = await _get_operation(session, tenant_id, idempotency_key)
            if concurrent is not None:
                _assert_same_operation(concurrent, operation, conversation_id)
                workspace = await _load_workspace(session, tenant_id, conversation_id)
                if workspace is None:
                    raise LookupError("conversation not found")
                return workspace
            if (
                conversation.ownership_mode != OwnershipMode.HUMAN.value
                or conversation.assigned_agent_id != actor_subject_id
            ):
                raise RuntimeError("conversation ownership changed; take over again")
            now = datetime.now(UTC)
            message_id = str(uuid5(NAMESPACE_URL, f"{tenant_id}:{idempotency_key}:agent-message"))
            session.add(
                MessageModel(
                    tenant_id=tenant_id,
                    message_id=message_id,
                    conversation_id=conversation_id,
                    role="agent",
                    content=redact_sensitive_text(content),
                    redaction_status="completed",
                    message_type="chat",
                    author_subject_id=actor_subject_id,
                    message_metadata={},
                    created_at=now,
                )
            )
            conversation.last_message_at = now
            if conversation.first_response_at is None:
                conversation.first_response_at = now
            if conversation.first_human_response_at is None:
                conversation.first_human_response_at = now
            conversation.updated_at = now
            conversation.unread_count = 0
            await record_human_message_experience(
                session,
                tenant_id=tenant_id,
                site_id=conversation.site_id or "default-site",
                conversation_id=conversation_id,
                message_id=message_id,
                content=content,
                actor_subject_id=actor_subject_id,
                occurred_at=now,
            )
            _record_write(
                session=session,
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                operation=operation,
                resource_type="conversation",
                resource_id=conversation_id,
                actor_subject_id=actor_subject_id,
                correlation_id=correlation_id,
                details={
                    "message_id": message_id,
                    "site_id": conversation.site_id or "default-site",
                },
                created_at=now,
            )
            await session.flush()
            workspace = await _load_workspace(session, tenant_id, conversation_id)
            if workspace is None:
                raise LookupError("conversation not found")
            return workspace

    async def append_internal_note(
        self,
        *,
        tenant_id: str,
        conversation_id: str,
        actor_subject_id: str,
        correlation_id: str,
        content: str,
        idempotency_key: str,
    ) -> ConversationWorkspace:
        return await self._append_private_message(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            actor_subject_id=actor_subject_id,
            correlation_id=correlation_id,
            content=content,
            idempotency_key=idempotency_key,
            message_type="internal_note",
            operation="conversation.internal_note_added",
        )

    async def _append_private_message(
        self,
        *,
        tenant_id: str,
        conversation_id: str,
        actor_subject_id: str,
        correlation_id: str,
        content: str,
        idempotency_key: str,
        message_type: str,
        operation: str,
    ) -> ConversationWorkspace:
        async with self._session_factory.begin() as session:
            existing = await _get_operation(session, tenant_id, idempotency_key)
            if existing is not None:
                _assert_same_operation(existing, operation, conversation_id)
                workspace = await _load_workspace(session, tenant_id, conversation_id)
                if workspace is None:
                    raise LookupError("conversation not found")
                return workspace
            conversation = await session.scalar(
                select(ConversationModel)
                .where(
                    ConversationModel.tenant_id == tenant_id,
                    ConversationModel.conversation_id == conversation_id,
                )
                .with_for_update()
            )
            if conversation is None:
                raise LookupError("conversation not found")
            now = datetime.now(UTC)
            message_id = str(uuid5(NAMESPACE_URL, f"{tenant_id}:{idempotency_key}:{message_type}"))
            session.add(
                MessageModel(
                    tenant_id=tenant_id,
                    message_id=message_id,
                    conversation_id=conversation_id,
                    role="agent",
                    content=redact_sensitive_text(content),
                    redaction_status="completed",
                    message_type=message_type,
                    author_subject_id=actor_subject_id,
                    message_metadata={"visibility": "operators_only"},
                    created_at=now,
                )
            )
            conversation.updated_at = now
            _record_write(
                session=session,
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                operation=operation,
                resource_type="conversation",
                resource_id=conversation_id,
                actor_subject_id=actor_subject_id,
                correlation_id=correlation_id,
                details={"message_id": message_id, "message_type": message_type},
                created_at=now,
            )
            await session.flush()
            workspace = await _load_workspace(session, tenant_id, conversation_id)
            if workspace is None:
                raise LookupError("conversation not found")
            return workspace

    async def update_conversation_routing(
        self,
        *,
        tenant_id: str,
        conversation_id: str,
        actor_subject_id: str,
        correlation_id: str,
        assigned_agent_id: str | None,
        queue_id: str | None,
        priority: ConversationPriority,
        tags: tuple[str, ...],
        idempotency_key: str,
        expected_routing_version: int | None = None,
        force: bool = False,
    ) -> ConversationWorkspace:
        operation = "conversation.routing_updated"
        async with self._session_factory.begin() as session:
            existing = await _get_operation(session, tenant_id, idempotency_key)
            if existing is not None:
                _assert_same_operation(existing, operation, conversation_id)
                workspace = await _load_workspace(session, tenant_id, conversation_id)
                if workspace is None:
                    raise LookupError("conversation not found")
                return workspace
            conversation = await session.scalar(
                select(ConversationModel)
                .where(
                    ConversationModel.tenant_id == tenant_id,
                    ConversationModel.conversation_id == conversation_id,
                )
                .with_for_update()
            )
            if conversation is None:
                raise LookupError("conversation not found")
            if (
                expected_routing_version is not None
                and conversation.routing_version != expected_routing_version
            ):
                raise RuntimeError("conversation changed; refresh before retrying")
            if (
                not force
                and conversation.assigned_agent_id is not None
                and conversation.assigned_agent_id != actor_subject_id
                and assigned_agent_id != conversation.assigned_agent_id
            ):
                raise RuntimeError("conversation is assigned to another agent")
            await _validate_routing_targets(
                session, tenant_id, assigned_agent_id, queue_id, conversation.site_id
            )
            now = datetime.now(UTC)
            conversation.assigned_agent_id = assigned_agent_id
            conversation.queue_id = queue_id
            conversation.priority = priority.value
            conversation.tags = list(tags)
            conversation.routing_version += 1
            if assigned_agent_id is not None:
                conversation.status = ConversationStatus.OPEN.value
                conversation.ownership_mode = OwnershipMode.HUMAN.value
            conversation.updated_at = now
            _record_write(
                session=session,
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                operation=operation,
                resource_type="conversation",
                resource_id=conversation_id,
                actor_subject_id=actor_subject_id,
                correlation_id=correlation_id,
                details={
                    "assigned_agent_id": assigned_agent_id,
                    "queue_id": queue_id,
                    "priority": priority.value,
                    "tags": list(tags),
                },
                created_at=now,
            )
            await session.flush()
            workspace = await _load_workspace(session, tenant_id, conversation_id)
            if workspace is None:
                raise LookupError("conversation not found")
            return workspace

    async def create_manual_handoff(
        self,
        *,
        tenant_id: str,
        conversation_id: str,
        actor_subject_id: str,
        correlation_id: str,
        summary: str,
        queue_id: str | None,
        priority: ConversationPriority,
        idempotency_key: str,
    ) -> ConversationWorkspace:
        operation = "handoff.created_manually"
        async with self._session_factory.begin() as session:
            existing = await _get_operation(session, tenant_id, idempotency_key)
            if existing is not None:
                _assert_same_operation(existing, operation, conversation_id)
                workspace = await _load_workspace(session, tenant_id, conversation_id)
                if workspace is None:
                    raise LookupError("conversation not found")
                return workspace
            conversation = await session.scalar(
                select(ConversationModel)
                .where(
                    ConversationModel.tenant_id == tenant_id,
                    ConversationModel.conversation_id == conversation_id,
                )
                .with_for_update()
            )
            if conversation is None:
                raise LookupError("conversation not found")
            await _validate_routing_targets(
                session, tenant_id, None, queue_id, conversation.site_id
            )
            now = datetime.now(UTC)
            open_handoff = await session.scalar(
                select(HandoffRequestModel)
                .where(
                    HandoffRequestModel.tenant_id == tenant_id,
                    HandoffRequestModel.conversation_id == conversation_id,
                    HandoffRequestModel.status.in_(("pending", "assigned")),
                )
                .order_by(HandoffRequestModel.created_at.desc())
                .with_for_update()
            )
            if open_handoff is not None:
                _record_write(
                    session=session,
                    tenant_id=tenant_id,
                    idempotency_key=idempotency_key,
                    operation=operation,
                    resource_type="conversation",
                    resource_id=conversation_id,
                    actor_subject_id=actor_subject_id,
                    correlation_id=correlation_id,
                    details={"handoff_id": open_handoff.handoff_id, "already_open": True},
                    created_at=now,
                )
                await session.flush()
                workspace = await _load_workspace(session, tenant_id, conversation_id)
                if workspace is None:
                    raise LookupError("conversation not found")
                return workspace
            handoff_id = str(uuid5(NAMESPACE_URL, f"{tenant_id}:{idempotency_key}:handoff"))
            session.add(
                HandoffRequestModel(
                    tenant_id=tenant_id,
                    handoff_id=handoff_id,
                    customer_id=conversation.customer_id,
                    conversation_id=conversation_id,
                    reason_code="manual_agent_handoff",
                    risk_level=conversation.risk_level,
                    summary=redact_sensitive_text(summary),
                    verified_evidence=[],
                    failed_tools=[],
                    knowledge_sources=[],
                    status="pending",
                    priority=priority.value,
                    queue_id=queue_id,
                    idempotency_key=str(uuid5(NAMESPACE_URL, idempotency_key)).replace("-", ""),
                    trace_id=f"manual:{correlation_id}",
                    created_at=now,
                    updated_at=now,
                )
            )
            conversation.status = ConversationStatus.WAITING_HUMAN.value
            conversation.ownership_mode = OwnershipMode.QUEUED.value
            conversation.assigned_agent_id = None
            conversation.queue_id = queue_id
            conversation.priority = priority.value
            conversation.updated_at = now
            _record_write(
                session=session,
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                operation=operation,
                resource_type="conversation",
                resource_id=conversation_id,
                actor_subject_id=actor_subject_id,
                correlation_id=correlation_id,
                details={"handoff_id": handoff_id, "queue_id": queue_id},
                created_at=now,
            )
            await session.flush()
            workspace = await _load_workspace(session, tenant_id, conversation_id)
            if workspace is None:
                raise LookupError("conversation not found")
            return workspace

    async def list_queues(self, *, tenant_id: str) -> list[SupportQueue]:
        async with self._session_factory() as session:
            models = list(
                await session.scalars(
                    select(SupportQueueModel)
                    .where(SupportQueueModel.tenant_id == tenant_id)
                    .order_by(
                        SupportQueueModel.status.asc(),
                        SupportQueueModel.is_default.desc(),
                        SupportQueueModel.name,
                    )
                )
            )
        return [_queue_to_domain(model) for model in models]

    async def create_queue(
        self,
        *,
        tenant_id: str,
        queue_id: str,
        name: str,
        description: str,
        is_default: bool,
        site_id: str | None,
        actor_subject_id: str,
        correlation_id: str,
        idempotency_key: str,
    ) -> SupportQueue:
        now = datetime.now(UTC)
        async with self._session_factory.begin() as session:
            existing = await _get_operation(session, tenant_id, idempotency_key)
            if existing is not None:
                _assert_same_operation(existing, "support_queue.created", queue_id)
                model = await session.scalar(
                    select(SupportQueueModel).where(
                        SupportQueueModel.tenant_id == tenant_id,
                        SupportQueueModel.queue_id == queue_id,
                    )
                )
                if model is None:
                    raise LookupError("support queue was not found")
                return _queue_to_domain(model)
            duplicate = await session.scalar(
                select(SupportQueueModel).where(
                    SupportQueueModel.tenant_id == tenant_id,
                    SupportQueueModel.name == name,
                    SupportQueueModel.status == "active",
                )
            )
            if duplicate is not None:
                raise RuntimeError("a queue with this name already exists")
            if site_id is not None:
                site = await session.scalar(
                    select(SupportSiteModel).where(
                        SupportSiteModel.tenant_id == tenant_id,
                        SupportSiteModel.site_id == site_id,
                        SupportSiteModel.status == "active",
                    )
                )
                if site is None:
                    raise LookupError("support site was not found")
            if is_default:
                defaults = list(
                    await session.scalars(
                        select(SupportQueueModel)
                        .where(
                            SupportQueueModel.tenant_id == tenant_id,
                            SupportQueueModel.is_default.is_(True),
                        )
                        .with_for_update()
                    )
                )
                for item in defaults:
                    item.is_default = False
            model = SupportQueueModel(
                tenant_id=tenant_id,
                queue_id=queue_id,
                name=name,
                description=description,
                site_id=site_id,
                is_default=is_default,
                status="active",
                created_at=now,
                updated_at=now,
            )
            session.add(model)
            _record_write(
                session=session,
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                operation="support_queue.created",
                resource_type="support_queue",
                resource_id=queue_id,
                actor_subject_id=actor_subject_id,
                correlation_id=correlation_id,
                details={"name": name, "is_default": is_default},
                created_at=now,
            )
            await session.flush()
            return _queue_to_domain(model)

    async def update_queue(
        self,
        *,
        tenant_id: str,
        queue_id: str,
        name: str | None,
        description: str | None,
        status: str | None,
        is_default: bool | None,
        site_id: str | None,
        actor_subject_id: str,
        correlation_id: str,
        idempotency_key: str,
    ) -> SupportQueue:
        now = datetime.now(UTC)
        async with self._session_factory.begin() as session:
            existing = await _get_operation(session, tenant_id, idempotency_key)
            if existing is not None:
                _assert_same_operation(existing, "support_queue.updated", queue_id)
            model = await session.scalar(
                select(SupportQueueModel)
                .where(
                    SupportQueueModel.tenant_id == tenant_id,
                    SupportQueueModel.queue_id == queue_id,
                )
                .with_for_update()
            )
            if model is None:
                raise LookupError("support queue was not found")
            if existing is None:
                if status == "disabled":
                    referenced = await session.scalar(
                        select(AutomationRuleModel.id).where(
                            AutomationRuleModel.tenant_id == tenant_id,
                            AutomationRuleModel.enabled.is_(True),
                            AutomationRuleModel.actions["queue_id"].as_string() == queue_id,
                        )
                    )
                    if referenced is not None:
                        raise RuntimeError("queue is referenced by an active automation rule")
                    if model.is_default:
                        raise RuntimeError("default queue cannot be disabled")
                if model.is_default and is_default is False:
                    raise RuntimeError("assign another default queue before clearing this one")
                if is_default:
                    defaults = list(
                        await session.scalars(
                            select(SupportQueueModel)
                            .where(
                                SupportQueueModel.tenant_id == tenant_id,
                                SupportQueueModel.queue_id != queue_id,
                                SupportQueueModel.is_default.is_(True),
                            )
                            .with_for_update()
                        )
                    )
                    for item in defaults:
                        item.is_default = False
                if site_id is not None and site_id != model.site_id:
                    referenced_rules = list(
                        await session.scalars(
                            select(AutomationRuleModel).where(
                                AutomationRuleModel.tenant_id == tenant_id,
                                AutomationRuleModel.enabled.is_(True),
                                AutomationRuleModel.actions["queue_id"].as_string() == queue_id,
                            )
                        )
                    )
                    if any(
                        (rule.conditions or {}).get("site_id") != site_id
                        for rule in referenced_rules
                    ):
                        raise RuntimeError(
                            "queue is referenced by an automation rule for another site"
                        )
                if name is not None:
                    model.name = name
                if description is not None:
                    model.description = description
                if status is not None:
                    model.status = status
                if is_default is not None:
                    model.is_default = is_default
                if site_id is not None:
                    site = await session.scalar(
                        select(SupportSiteModel).where(
                            SupportSiteModel.tenant_id == tenant_id,
                            SupportSiteModel.site_id == site_id,
                            SupportSiteModel.status == "active",
                        )
                    )
                    if site is None:
                        raise LookupError("support site was not found")
                    model.site_id = site_id
                model.updated_at = now
                _record_write(
                    session=session,
                    tenant_id=tenant_id,
                    idempotency_key=idempotency_key,
                    operation="support_queue.updated",
                    resource_type="support_queue",
                    resource_id=queue_id,
                    actor_subject_id=actor_subject_id,
                    correlation_id=correlation_id,
                    details={"status": status, "is_default": is_default},
                    created_at=now,
                )
                await session.flush()
            return _queue_to_domain(model)

    async def list_queue_members(
        self, *, tenant_id: str, queue_id: str
    ) -> list[SupportQueueMember]:
        async with self._session_factory() as session:
            queue_exists = await session.scalar(
                select(SupportQueueModel.id).where(
                    SupportQueueModel.tenant_id == tenant_id,
                    SupportQueueModel.queue_id == queue_id,
                )
            )
            if queue_exists is None:
                raise LookupError("support queue was not found")
            rows = list(
                await session.execute(
                    select(SupportQueueMembershipModel, IdentityUserModel)
                    .join(
                        IdentityUserModel,
                        IdentityUserModel.user_id == SupportQueueMembershipModel.user_id,
                    )
                    .where(
                        SupportQueueMembershipModel.tenant_id == tenant_id,
                        SupportQueueMembershipModel.queue_id == queue_id,
                        SupportQueueMembershipModel.status == "active",
                        IdentityUserModel.status == "active",
                    )
                    .order_by(IdentityUserModel.display_name)
                )
            )
        return [SupportQueueMember(m.user_id, u.display_name, m.role, m.status) for m, u in rows]

    async def update_queue_members(
        self,
        *,
        tenant_id: str,
        queue_id: str,
        agent_ids: tuple[str, ...],
        actor_subject_id: str,
        correlation_id: str,
        idempotency_key: str,
    ) -> list[SupportQueueMember]:
        now = datetime.now(UTC)
        async with self._session_factory.begin() as session:
            existing = await _get_operation(session, tenant_id, idempotency_key)
            if existing is not None:
                _assert_same_operation(existing, "support_queue.members_updated", queue_id)
            queue = await session.scalar(
                select(SupportQueueModel)
                .where(
                    SupportQueueModel.tenant_id == tenant_id,
                    SupportQueueModel.queue_id == queue_id,
                )
                .with_for_update()
            )
            if queue is None:
                raise LookupError("support queue was not found")
            memberships = list(
                await session.scalars(
                    select(TenantMembershipModel).where(
                        TenantMembershipModel.tenant_id == tenant_id,
                        TenantMembershipModel.user_id.in_(agent_ids or ("",)),
                        TenantMembershipModel.status == "active",
                    )
                )
            )
            valid_ids = {
                item.user_id
                for item in memberships
                if {
                    "support:inbox:read",
                    "support:inbox:reply:self",
                    "support:inbox:takeover",
                    "support:inbox:manage",
                }.intersection(set(item.scopes or []))
            }
            invalid = set(agent_ids) - valid_ids
            if invalid:
                raise LookupError("one or more queue members are not active support agents")
            if existing is None:
                current = list(
                    await session.scalars(
                        select(SupportQueueMembershipModel)
                        .where(
                            SupportQueueMembershipModel.tenant_id == tenant_id,
                            SupportQueueMembershipModel.queue_id == queue_id,
                        )
                        .with_for_update()
                    )
                )
                by_user = {item.user_id: item for item in current}
                for item in current:
                    if item.user_id not in valid_ids:
                        item.status = "disabled"
                        item.updated_at = now
                for user_id in agent_ids:
                    item = by_user.get(user_id)
                    if item is None:
                        session.add(
                            SupportQueueMembershipModel(
                                tenant_id=tenant_id,
                                queue_id=queue_id,
                                user_id=user_id,
                                role="member",
                                status="active",
                                created_at=now,
                                updated_at=now,
                            )
                        )
                    else:
                        item.status = "active"
                        item.updated_at = now
                _record_write(
                    session=session,
                    tenant_id=tenant_id,
                    idempotency_key=idempotency_key,
                    operation="support_queue.members_updated",
                    resource_type="support_queue",
                    resource_id=queue_id,
                    actor_subject_id=actor_subject_id,
                    correlation_id=correlation_id,
                    details={"agent_ids": list(agent_ids)},
                    created_at=now,
                )
            await session.flush()
            rows = list(
                await session.execute(
                    select(SupportQueueMembershipModel, IdentityUserModel)
                    .join(
                        IdentityUserModel,
                        IdentityUserModel.user_id == SupportQueueMembershipModel.user_id,
                    )
                    .where(
                        SupportQueueMembershipModel.tenant_id == tenant_id,
                        SupportQueueMembershipModel.queue_id == queue_id,
                        SupportQueueMembershipModel.status == "active",
                    )
                    .order_by(IdentityUserModel.display_name)
                )
            )
            return [
                SupportQueueMember(m.user_id, u.display_name, m.role, m.status) for m, u in rows
            ]

    async def list_agent_options(self, *, tenant_id: str) -> list[SupportAgentOption]:
        async with self._session_factory() as session:
            rows = list(
                await session.execute(
                    select(TenantMembershipModel, IdentityUserModel)
                    .join(
                        IdentityUserModel,
                        IdentityUserModel.user_id == TenantMembershipModel.user_id,
                    )
                    .where(
                        TenantMembershipModel.tenant_id == tenant_id,
                        TenantMembershipModel.status == "active",
                        IdentityUserModel.status == "active",
                    )
                    .order_by(IdentityUserModel.display_name)
                )
            )
        return [
            SupportAgentOption(membership.user_id, user.display_name)
            for membership, user in rows
            if {
                "support:inbox:reply:self",
                "support:inbox:takeover",
                "support:inbox:manage",
            }.intersection(set(membership.scopes or []))
        ]

    async def list_canned_replies(self, *, tenant_id: str) -> list[CannedReply]:
        async with self._session_factory() as session:
            models = list(
                await session.scalars(
                    select(CannedReplyModel)
                    .where(
                        CannedReplyModel.tenant_id == tenant_id,
                        CannedReplyModel.status == "active",
                    )
                    .order_by(CannedReplyModel.title)
                )
            )
        return [_canned_reply_to_domain(model) for model in models]

    async def create_canned_reply(
        self,
        *,
        tenant_id: str,
        actor_subject_id: str,
        correlation_id: str,
        reply_id: str,
        title: str,
        content: str,
        shortcut: str,
        idempotency_key: str,
    ) -> CannedReply:
        operation = "canned_reply.created"
        async with self._session_factory.begin() as session:
            existing = await _get_operation(session, tenant_id, idempotency_key)
            if existing is not None:
                _assert_same_operation(existing, operation, reply_id)
                model = await session.scalar(
                    select(CannedReplyModel).where(
                        CannedReplyModel.tenant_id == tenant_id,
                        CannedReplyModel.reply_id == reply_id,
                    )
                )
                if model is None:
                    raise LookupError("canned reply not found")
                return _canned_reply_to_domain(model)
            duplicate = await session.scalar(
                select(CannedReplyModel).where(
                    CannedReplyModel.tenant_id == tenant_id,
                    CannedReplyModel.shortcut == shortcut,
                    CannedReplyModel.status == "active",
                )
            )
            if duplicate is not None:
                raise RuntimeError("canned reply shortcut already exists")
            now = datetime.now(UTC)
            model = CannedReplyModel(
                tenant_id=tenant_id,
                reply_id=reply_id,
                title=title,
                content=redact_sensitive_text(content),
                shortcut=shortcut,
                status="active",
                created_at=now,
                updated_at=now,
            )
            session.add(model)
            _record_write(
                session=session,
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                operation=operation,
                resource_type="canned_reply",
                resource_id=reply_id,
                actor_subject_id=actor_subject_id,
                correlation_id=correlation_id,
                details={"title": title, "shortcut": shortcut},
                created_at=now,
            )
            await session.flush()
            return _canned_reply_to_domain(model)

    async def list_customer_memory(
        self, *, tenant_id: str, customer_id: str
    ) -> list[CustomerMemoryItem]:
        async with self._session_factory() as session:
            models = list(
                await session.scalars(
                    select(CustomerMemoryItemModel)
                    .where(
                        CustomerMemoryItemModel.tenant_id == tenant_id,
                        CustomerMemoryItemModel.customer_id == customer_id,
                    )
                    .order_by(CustomerMemoryItemModel.updated_at.desc())
                )
            )
        return [_memory_to_domain(model) for model in models]

    async def upsert_customer_memory(
        self,
        *,
        tenant_id: str,
        customer_id: str,
        actor_subject_id: str,
        correlation_id: str,
        memory_id: str,
        kind: MemoryKind,
        content: str,
        source_type: str,
        source_id: str,
        confidence: float,
        consent_status: str,
        idempotency_key: str,
    ) -> CustomerMemoryItem:
        operation = "customer_memory.upserted"
        async with self._session_factory.begin() as session:
            existing = await _get_operation(session, tenant_id, idempotency_key)
            if existing is not None:
                _assert_same_operation(existing, operation, memory_id)
                model = await _get_memory(session, tenant_id, customer_id, memory_id)
                if model is None:
                    raise LookupError("memory not found")
                return _memory_to_domain(model)
            customer = await session.scalar(
                select(CustomerModel)
                .where(
                    CustomerModel.tenant_id == tenant_id,
                    CustomerModel.customer_id == customer_id,
                )
                .with_for_update()
            )
            if customer is None:
                raise LookupError("trusted customer not found")
            concurrent = await _get_operation(session, tenant_id, idempotency_key)
            if concurrent is not None:
                _assert_same_operation(concurrent, operation, memory_id)
                model = await _get_memory(session, tenant_id, customer_id, memory_id)
                if model is None:
                    raise LookupError("memory not found")
                return _memory_to_domain(model)
            now = datetime.now(UTC)
            expires_at = now + timedelta(days=_memory_ttl_days(kind))
            model = await _get_memory(session, tenant_id, customer_id, memory_id)
            if model is None:
                model = CustomerMemoryItemModel(
                    tenant_id=tenant_id,
                    customer_id=customer_id,
                    memory_id=memory_id,
                    kind=kind.value,
                    content=content,
                    source_type=source_type,
                    source_id=source_id,
                    confidence=confidence,
                    consent_status=consent_status,
                    status="active",
                    normalized_value={"text": content},
                    sensitivity="standard",
                    expires_at=expires_at,
                    last_verified_at=now,
                    legacy=False,
                    created_at=now,
                    updated_at=now,
                )
                session.add(model)
            else:
                model.kind = kind.value
                model.content = content
                model.source_type = source_type
                model.source_id = source_id
                model.confidence = confidence
                model.consent_status = consent_status
                model.status = "active"
                model.normalized_value = {"text": content}
                model.sensitivity = "standard"
                model.expires_at = expires_at
                model.last_verified_at = now
                model.legacy = False
                model.updated_at = now
            _record_write(
                session=session,
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                operation=operation,
                resource_type="customer_memory",
                resource_id=memory_id,
                actor_subject_id=actor_subject_id,
                correlation_id=correlation_id,
                details={"customer_id": customer_id, "kind": kind.value},
                created_at=now,
            )
            await session.flush()
            return _memory_to_domain(model)

    async def delete_customer_memory(
        self,
        *,
        tenant_id: str,
        customer_id: str,
        memory_id: str,
        actor_subject_id: str,
        correlation_id: str,
        idempotency_key: str,
    ) -> bool:
        operation = "customer_memory.deleted"
        async with self._session_factory.begin() as session:
            existing = await _get_operation(session, tenant_id, idempotency_key)
            if existing is not None:
                _assert_same_operation(existing, operation, memory_id)
                return True
            model = await _get_memory(session, tenant_id, customer_id, memory_id)
            now = datetime.now(UTC)
            if model is not None:
                await session.delete(model)
            _record_write(
                session=session,
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                operation=operation,
                resource_type="customer_memory",
                resource_id=memory_id,
                actor_subject_id=actor_subject_id,
                correlation_id=correlation_id,
                details={"customer_id": customer_id, "existed": model is not None},
                created_at=now,
            )
            return True

    async def list_memory_candidates(
        self, *, tenant_id: str, customer_id: str
    ) -> list[MemoryCandidate]:
        async with self._session_factory() as session:
            models = list(
                await session.scalars(
                    select(MemoryCandidateModel)
                    .where(
                        MemoryCandidateModel.tenant_id == tenant_id,
                        MemoryCandidateModel.customer_id == customer_id,
                    )
                    .order_by(MemoryCandidateModel.created_at.desc())
                    .limit(100)
                )
            )
        return [_candidate_to_domain(model) for model in models]

    async def propose_memory_candidate(
        self,
        *,
        tenant_id: str,
        customer_id: str,
        conversation_id: str,
        actor_subject_id: str,
        correlation_id: str,
        candidate_id: str,
        candidate_fingerprint: str,
        kind: MemoryKind,
        normalized_value: dict[str, object],
        display_text: str,
        source_message_ids: tuple[str, ...],
        source_trace_id: str,
        confidence: float,
        sensitivity: str,
        consent_required: bool,
        idempotency_key: str,
    ) -> MemoryCandidate:
        operation = "memory_candidate.proposed"
        async with self._session_factory.begin() as session:
            existing_operation = await _get_operation(session, tenant_id, idempotency_key)
            if existing_operation is not None:
                _assert_same_operation(existing_operation, operation, candidate_id)
                existing = await _get_candidate(session, tenant_id, customer_id, candidate_id)
                if existing is None:
                    raise LookupError("memory candidate not found")
                return _candidate_to_domain(existing)
            conversation = await session.scalar(
                select(ConversationModel).where(
                    ConversationModel.tenant_id == tenant_id,
                    ConversationModel.conversation_id == conversation_id,
                    ConversationModel.customer_id == customer_id,
                )
            )
            if conversation is None:
                raise LookupError("trusted customer conversation not found")
            existing = await session.scalar(
                select(MemoryCandidateModel).where(
                    MemoryCandidateModel.tenant_id == tenant_id,
                    MemoryCandidateModel.customer_id == customer_id,
                    MemoryCandidateModel.candidate_fingerprint == candidate_fingerprint,
                )
            )
            now = datetime.now(UTC)
            if existing is None:
                existing = MemoryCandidateModel(
                    tenant_id=tenant_id,
                    candidate_id=candidate_id,
                    candidate_fingerprint=candidate_fingerprint,
                    customer_id=customer_id,
                    conversation_id=conversation_id,
                    kind=kind.value,
                    normalized_value=dict(normalized_value),
                    display_text=redact_sensitive_text(display_text),
                    source_message_ids=list(source_message_ids),
                    source_trace_id=source_trace_id,
                    confidence=confidence,
                    sensitivity=sensitivity,
                    consent_required=consent_required,
                    status=(
                        MemoryCandidateStatus.AWAITING_CONSENT.value
                        if consent_required
                        else MemoryCandidateStatus.PROPOSED.value
                    ),
                    created_at=now,
                    expires_at=now + timedelta(days=7),
                )
                session.add(existing)
            _record_write(
                session=session,
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                operation=operation,
                resource_type="memory_candidate",
                resource_id=existing.candidate_id,
                actor_subject_id=actor_subject_id,
                correlation_id=correlation_id,
                details={"customer_id": customer_id, "kind": kind.value},
                created_at=now,
            )
            await session.flush()
            return _candidate_to_domain(existing)

    async def review_memory_candidate(
        self,
        *,
        tenant_id: str,
        customer_id: str,
        candidate_id: str,
        actor_subject_id: str,
        correlation_id: str,
        approve: bool,
        rejection_reason: str | None,
        idempotency_key: str,
    ) -> tuple[MemoryCandidate, CustomerMemoryItem | None]:
        operation = "memory_candidate.approved" if approve else "memory_candidate.rejected"
        memory_id = str(uuid5(NAMESPACE_URL, f"{tenant_id}:{customer_id}:{candidate_id}:memory"))
        expired = False
        result: tuple[MemoryCandidate, CustomerMemoryItem | None] | None = None
        async with self._session_factory.begin() as session:
            existing_operation = await _get_operation(session, tenant_id, idempotency_key)
            if existing_operation is not None:
                _assert_same_operation(existing_operation, operation, candidate_id)
                candidate = await _get_candidate(session, tenant_id, customer_id, candidate_id)
                if candidate is None:
                    raise LookupError("memory candidate not found")
                memory = await _get_memory(session, tenant_id, customer_id, memory_id)
                return _candidate_to_domain(candidate), (
                    None if memory is None else _memory_to_domain(memory)
                )
            candidate = await session.scalar(
                select(MemoryCandidateModel)
                .where(
                    MemoryCandidateModel.tenant_id == tenant_id,
                    MemoryCandidateModel.customer_id == customer_id,
                    MemoryCandidateModel.candidate_id == candidate_id,
                )
                .with_for_update()
            )
            if candidate is None:
                raise LookupError("memory candidate not found")
            now = datetime.now(UTC)
            memory: CustomerMemoryItemModel | None = None
            if candidate.expires_at <= now:
                candidate.status = MemoryCandidateStatus.EXPIRED.value
                expired = True
            else:
                target_status = (
                    MemoryCandidateStatus.APPROVED if approve else MemoryCandidateStatus.REJECTED
                )
                if (
                    candidate.status
                    in {
                        MemoryCandidateStatus.APPROVED.value,
                        MemoryCandidateStatus.REJECTED.value,
                    }
                    and candidate.status != target_status.value
                ):
                    raise ValueError("memory candidate was already reviewed with another decision")
                candidate.status = target_status.value
                candidate.rejection_reason = None if approve else rejection_reason or "not_approved"
                candidate.reviewed_by = actor_subject_id
                candidate.reviewed_at = now
                if approve:
                    kind = MemoryKind(candidate.kind)
                    memory = await _get_memory(session, tenant_id, customer_id, memory_id)
                    if memory is None:
                        memory = CustomerMemoryItemModel(
                            tenant_id=tenant_id,
                            customer_id=customer_id,
                            memory_id=memory_id,
                            kind=kind.value,
                            content=candidate.display_text,
                            source_type="resolved_conversation",
                            source_id=candidate.candidate_id,
                            confidence=candidate.confidence,
                            consent_status="granted",
                            status="active",
                            normalized_value=dict(candidate.normalized_value or {}),
                            sensitivity=candidate.sensitivity,
                            expires_at=now + timedelta(days=_memory_ttl_days(kind)),
                            last_verified_at=now,
                            legacy=False,
                            created_at=now,
                            updated_at=now,
                        )
                        session.add(memory)
            _record_write(
                session=session,
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                operation="memory_candidate.expired" if expired else operation,
                resource_type="memory_candidate",
                resource_id=candidate_id,
                actor_subject_id=actor_subject_id,
                correlation_id=correlation_id,
                details={
                    "customer_id": customer_id,
                    "approved": approve,
                    "expired": expired,
                },
                created_at=now,
            )
            await session.flush()
            result = (
                _candidate_to_domain(candidate),
                (None if memory is None else _memory_to_domain(memory)),
            )
        if expired:
            raise ValueError("memory candidate has expired")
        if result is None:
            raise RuntimeError("memory candidate review did not produce a result")
        return result

    async def list_resolution_episodes(
        self, *, tenant_id: str, customer_id: str
    ) -> list[ResolutionEpisode]:
        now = datetime.now(UTC)
        async with self._session_factory() as session:
            models = list(
                await session.scalars(
                    select(ResolutionEpisodeModel)
                    .where(
                        ResolutionEpisodeModel.tenant_id == tenant_id,
                        ResolutionEpisodeModel.customer_id == customer_id,
                        ResolutionEpisodeModel.status == "active",
                        ResolutionEpisodeModel.expires_at > now,
                    )
                    .order_by(ResolutionEpisodeModel.resolved_at.desc())
                    .limit(50)
                )
            )
        return [_episode_to_domain(model) for model in models]

    async def delete_resolution_episode(
        self,
        *,
        tenant_id: str,
        customer_id: str,
        episode_id: str,
        actor_subject_id: str,
        correlation_id: str,
        idempotency_key: str,
    ) -> bool:
        operation = "resolution_episode.deleted"
        async with self._session_factory.begin() as session:
            existing_operation = await _get_operation(session, tenant_id, idempotency_key)
            if existing_operation is not None:
                _assert_same_operation(existing_operation, operation, episode_id)
                return True
            episode = await session.scalar(
                select(ResolutionEpisodeModel).where(
                    ResolutionEpisodeModel.tenant_id == tenant_id,
                    ResolutionEpisodeModel.customer_id == customer_id,
                    ResolutionEpisodeModel.episode_id == episode_id,
                )
            )
            if episode is not None:
                await session.delete(episode)
            now = datetime.now(UTC)
            _record_write(
                session=session,
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                operation=operation,
                resource_type="resolution_episode",
                resource_id=episode_id,
                actor_subject_id=actor_subject_id,
                correlation_id=correlation_id,
                details={"customer_id": customer_id, "existed": episode is not None},
                created_at=now,
            )
            return True


async def _load_workspace(
    session: AsyncSession, tenant_id: str, conversation_id: str
) -> ConversationWorkspace | None:
    row = (
        await session.execute(
            select(ConversationModel, CustomerModel.display_name)
            .outerjoin(
                CustomerModel,
                (CustomerModel.tenant_id == ConversationModel.tenant_id)
                & (CustomerModel.customer_id == ConversationModel.customer_id),
            )
            .where(
                ConversationModel.tenant_id == tenant_id,
                ConversationModel.conversation_id == conversation_id,
            )
        )
    ).one_or_none()
    if row is None:
        return None
    conversation, display_name = row
    messages = list(
        await session.scalars(
            select(MessageModel)
            .where(
                MessageModel.tenant_id == tenant_id,
                MessageModel.conversation_id == conversation_id,
            )
            .order_by(MessageModel.created_at.asc(), MessageModel.id.asc())
        )
    )
    handoff = await session.scalar(
        select(HandoffRequestModel)
        .where(
            HandoffRequestModel.tenant_id == tenant_id,
            HandoffRequestModel.conversation_id == conversation_id,
            HandoffRequestModel.status.in_(("pending", "assigned")),
        )
        .order_by(HandoffRequestModel.created_at.desc())
    )
    preview = next(
        (message.content for message in reversed(messages) if message.message_type == "chat"),
        None,
    )
    handoff_context = None
    if handoff is not None:
        handoff_context = {
            "handoff_id": handoff.handoff_id,
            "reason_code": handoff.reason_code,
            "summary": redact_sensitive_text(handoff.summary),
            "risk_level": handoff.risk_level,
            "user_intent": handoff.user_intent,
            "unresolved_question": redact_sensitive_text(handoff.unresolved_question or ""),
            "ai_attempt": redact_sensitive_text(handoff.ai_attempt or ""),
            "suggested_next_action": handoff.suggested_next_action,
            "reply_draft": redact_sensitive_text(handoff.reply_draft or ""),
            "priority": handoff.priority,
            "queue_id": handoff.queue_id,
            "failed_tools": list(handoff.failed_tools or []),
            "knowledge_sources": list(handoff.knowledge_sources or []),
            "context_schema_version": handoff.context_schema_version,
            "customer_language": handoff.customer_language,
            "customer_region": handoff.customer_region,
            "identity_status": handoff.identity_status,
            "product_ids": list(handoff.product_ids or []),
            "order_id": handoff.order_id,
            "confirmed_fields": list(handoff.confirmed_fields or []),
            "customer_sentiment": handoff.customer_sentiment,
            "customer_request": redact_sensitive_text(handoff.customer_request or ""),
            "commitment_deadline": (
                handoff.commitment_deadline.isoformat() if handoff.commitment_deadline else None
            ),
        }
    return ConversationWorkspace(
        conversation=_conversation_to_domain(conversation, display_name, preview),
        messages=tuple(_message_to_domain(message) for message in messages),
        handoff_context=handoff_context,
    )


async def _get_operation(
    session: AsyncSession, tenant_id: str, idempotency_key: str
) -> SupportOperationRequestModel | None:
    return await session.scalar(
        select(SupportOperationRequestModel).where(
            SupportOperationRequestModel.tenant_id == tenant_id,
            SupportOperationRequestModel.idempotency_key == idempotency_key,
        )
    )


async def _get_memory(
    session: AsyncSession, tenant_id: str, customer_id: str, memory_id: str
) -> CustomerMemoryItemModel | None:
    return await session.scalar(
        select(CustomerMemoryItemModel).where(
            CustomerMemoryItemModel.tenant_id == tenant_id,
            CustomerMemoryItemModel.customer_id == customer_id,
            CustomerMemoryItemModel.memory_id == memory_id,
        )
    )


async def _get_candidate(
    session: AsyncSession,
    tenant_id: str,
    customer_id: str,
    candidate_id: str,
) -> MemoryCandidateModel | None:
    return await session.scalar(
        select(MemoryCandidateModel).where(
            MemoryCandidateModel.tenant_id == tenant_id,
            MemoryCandidateModel.customer_id == customer_id,
            MemoryCandidateModel.candidate_id == candidate_id,
        )
    )


def _assert_same_operation(
    existing: SupportOperationRequestModel, operation: str, resource_id: str
) -> None:
    if existing.operation != operation or existing.resource_id != resource_id:
        raise RuntimeError("idempotency key was already used for another operation")


def _encode_page_cursor(kind: str, value: object, identifier: object) -> str:
    encoded_value = (
        "null"
        if value is None
        else (value.isoformat() if isinstance(value, datetime) else str(value))
    )
    payload = json.dumps(
        {"kind": kind, "value": encoded_value, "id": str(identifier)},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_page_cursor(cursor: str, expected_kind: str) -> tuple[str, str]:
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(cursor + padding).decode("utf-8"))
        if payload.get("kind") != expected_kind:
            raise ValueError
        value = str(payload["value"])
        identifier = str(payload["id"])
        if len(value) > 80 or len(identifier) > 120:
            raise ValueError
        return value, identifier
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid pagination cursor") from exc


def _record_write(
    *,
    session: AsyncSession,
    tenant_id: str,
    idempotency_key: str,
    operation: str,
    resource_type: str,
    resource_id: str,
    actor_subject_id: str,
    correlation_id: str,
    details: dict,
    created_at: datetime,
) -> None:
    records = [
        SupportOperationRequestModel(
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            operation=operation,
            resource_type=resource_type,
            resource_id=resource_id,
            response_snapshot=details,
            created_at=created_at,
        ),
        AuditEventModel(
            tenant_id=tenant_id,
            event_id=str(uuid4()),
            event_type=operation,
            actor_subject_id=actor_subject_id,
            correlation_id=correlation_id,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
            created_at=created_at,
        ),
    ]
    session.add_all(records)


def _site_to_domain(model: SupportSiteModel) -> SupportSite:
    return SupportSite(
        site_id=model.site_id,
        tenant_id=model.tenant_id,
        name=model.name,
        base_url=model.base_url,
        status=model.status,
        created_at=model.created_at,
        updated_at=model.updated_at,
        primary_language=model.primary_language,
        verification_status=model.verification_status,
        duplicate_product_policy=model.duplicate_product_policy,
        duplicate_product_order=model.duplicate_product_order,
    )


def _conversation_to_domain(
    model: ConversationModel,
    display_name: str | None,
    preview: str | None,
    sla_due_at: datetime | None = None,
    handoff_reason: str | None = None,
) -> InboxConversation:
    return InboxConversation(
        conversation_id=model.conversation_id,
        tenant_id=model.tenant_id,
        site_id=model.site_id,
        customer_id=model.customer_id,
        customer_display_name=display_name,
        visitor_ip_address=model.visitor_ip_address,
        visitor_country_code=model.visitor_country_code,
        channel=model.channel,
        status=ConversationStatus(model.status),
        ownership_mode=OwnershipMode(model.ownership_mode),
        assigned_agent_id=model.assigned_agent_id,
        queue_id=model.queue_id,
        priority=ConversationPriority(model.priority or "normal"),
        tags=tuple(str(value) for value in (model.tags or [])),
        risk_level=model.risk_level,
        unread_count=model.unread_count,
        identity_verified=model.identity_verified,
        last_message_preview=(preview[:200] if preview else None),
        last_message_at=model.last_message_at,
        first_response_at=model.first_response_at,
        first_human_response_at=model.first_human_response_at,
        resolved_at=model.resolved_at,
        last_read_at=model.last_read_at,
        updated_at=model.updated_at,
        routing_version=model.routing_version,
        handoff_reason=handoff_reason,
        sla_due_at=sla_due_at,
    )


def _message_to_domain(model: MessageModel) -> SupportMessage:
    return SupportMessage(
        message_id=model.message_id,
        conversation_id=model.conversation_id,
        role=model.role,
        content=model.content,
        message_type=model.message_type,
        author_subject_id=model.author_subject_id,
        metadata=dict(model.message_metadata or {}),
        created_at=model.created_at,
    )


def _memory_to_domain(model: CustomerMemoryItemModel) -> CustomerMemoryItem:
    return CustomerMemoryItem(
        memory_id=model.memory_id,
        tenant_id=model.tenant_id,
        customer_id=model.customer_id,
        kind=MemoryKind(model.kind),
        content=model.content,
        source_type=model.source_type,
        source_id=model.source_id,
        confidence=model.confidence,
        consent_status=model.consent_status,
        expires_at=model.expires_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
        status=model.status,
        normalized_value=dict(model.normalized_value or {}),
        sensitivity=model.sensitivity,
        last_verified_at=model.last_verified_at,
        last_used_at=model.last_used_at,
        use_count=model.use_count,
        superseded_by=model.superseded_by,
        legacy=model.legacy,
    )


def _candidate_to_domain(model: MemoryCandidateModel) -> MemoryCandidate:
    return MemoryCandidate(
        candidate_id=model.candidate_id,
        tenant_id=model.tenant_id,
        customer_id=model.customer_id,
        conversation_id=model.conversation_id,
        kind=MemoryKind(model.kind),
        normalized_value=dict(model.normalized_value or {}),
        display_text=model.display_text,
        source_message_ids=tuple(str(value) for value in model.source_message_ids or []),
        source_trace_id=model.source_trace_id,
        confidence=model.confidence,
        sensitivity=model.sensitivity,
        consent_required=model.consent_required,
        status=MemoryCandidateStatus(model.status),
        rejection_reason=model.rejection_reason,
        reviewed_by=model.reviewed_by,
        reviewed_at=model.reviewed_at,
        created_at=model.created_at,
        expires_at=model.expires_at,
    )


def _episode_to_domain(model: ResolutionEpisodeModel) -> ResolutionEpisode:
    return ResolutionEpisode(
        episode_id=model.episode_id,
        tenant_id=model.tenant_id,
        customer_id=model.customer_id,
        conversation_id=model.conversation_id,
        intent=model.intent,
        issue_summary=model.issue_summary,
        product_reference=model.product_reference,
        order_reference=model.order_reference,
        actions_taken=tuple(str(value) for value in model.actions_taken or []),
        resolution_summary=model.resolution_summary,
        resolution_source=model.resolution_source,
        resolved_at=model.resolved_at,
        reopened_at=model.reopened_at,
        confidence=model.confidence,
        expires_at=model.expires_at,
        status=model.status,
    )


def _memory_ttl_days(kind: MemoryKind) -> int:
    return {
        MemoryKind.PREFERENCE: 90,
        MemoryKind.VERIFIED_PRODUCT: 180,
        MemoryKind.TROUBLESHOOTING: 30,
        MemoryKind.RESOLUTION: 90,
    }[kind]


async def _create_manual_resolution_episode(
    *,
    session: AsyncSession,
    conversation: ConversationModel,
    resolved_at: datetime,
    actor_subject_id: str,
) -> None:
    if conversation.customer_id is None:
        return
    episode_id = str(
        uuid5(
            NAMESPACE_URL,
            f"{conversation.tenant_id}:{conversation.conversation_id}:manual_resolution",
        )
    )
    existing = await session.scalar(
        select(ResolutionEpisodeModel).where(
            ResolutionEpisodeModel.tenant_id == conversation.tenant_id,
            ResolutionEpisodeModel.episode_id == episode_id,
        )
    )
    if existing is not None:
        return
    messages = list(
        await session.scalars(
            select(MessageModel)
            .where(
                MessageModel.tenant_id == conversation.tenant_id,
                MessageModel.conversation_id == conversation.conversation_id,
                MessageModel.message_type == "chat",
            )
            .order_by(MessageModel.id.desc())
            .limit(2)
        )
    )
    latest_user = next((item for item in messages if item.role == "user"), None)
    latest_assistant = next((item for item in messages if item.role == "assistant"), None)
    if latest_user is None or latest_assistant is None:
        return
    session.add(
        ResolutionEpisodeModel(
            tenant_id=conversation.tenant_id,
            episode_id=episode_id,
            customer_id=conversation.customer_id,
            conversation_id=conversation.conversation_id,
            intent=classify_conversation_intent(latest_user.content),
            issue_summary=redact_sensitive_text(latest_user.content)[:2000],
            product_reference=(extract_product_skus(latest_user.content) or (None,))[0],
            order_reference=None,
            actions_taken=[],
            resolution_summary=redact_sensitive_text(latest_assistant.content)[:3000],
            resolution_source="manual_resolution",
            resolved_at=resolved_at,
            reopened_at=None,
            confidence=0.9,
            expires_at=resolved_at + timedelta(days=90),
            status="active",
        )
    )


async def _validate_routing_targets(
    session: AsyncSession,
    tenant_id: str,
    assigned_agent_id: str | None,
    queue_id: str | None,
    site_id: str | None = None,
) -> None:
    if assigned_agent_id is not None:
        agent = await session.scalar(
            select(TenantMembershipModel).where(
                TenantMembershipModel.tenant_id == tenant_id,
                TenantMembershipModel.user_id == assigned_agent_id,
                TenantMembershipModel.status == "active",
            )
        )
        if agent is None or not {
            "support:inbox:reply:self",
            "support:inbox:takeover",
            "support:inbox:manage",
        }.intersection(set(agent.scopes or [])):
            raise PermissionError("assigned agent is not available")
    if queue_id is not None:
        queue = await session.scalar(
            select(SupportQueueModel).where(
                SupportQueueModel.tenant_id == tenant_id,
                SupportQueueModel.queue_id == queue_id,
                SupportQueueModel.status == "active",
            )
        )
        if queue is None:
            raise LookupError("support queue not found")
        if queue.site_id is not None and queue.site_id != site_id:
            raise LookupError("support queue is not available for this site")
    if assigned_agent_id is not None and queue_id is not None:
        membership = await session.scalar(
            select(SupportQueueMembershipModel.id).where(
                SupportQueueMembershipModel.tenant_id == tenant_id,
                SupportQueueMembershipModel.queue_id == queue_id,
                SupportQueueMembershipModel.user_id == assigned_agent_id,
                SupportQueueMembershipModel.status == "active",
            )
        )
        if membership is None:
            raise PermissionError("assigned agent is not an active member of the support queue")


def _queue_to_domain(model: SupportQueueModel) -> SupportQueue:
    return SupportQueue(
        queue_id=model.queue_id,
        tenant_id=model.tenant_id,
        name=model.name,
        description=model.description,
        is_default=model.is_default,
        status=model.status,
        site_id=model.site_id,
    )


def _canned_reply_to_domain(model: CannedReplyModel) -> CannedReply:
    return CannedReply(
        reply_id=model.reply_id,
        tenant_id=model.tenant_id,
        title=model.title,
        content=redact_sensitive_text(model.content),
        shortcut=model.shortcut,
        status=model.status,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )
