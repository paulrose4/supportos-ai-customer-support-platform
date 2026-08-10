from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.models import (
    AgentResponse,
    AuthenticatedPrincipal,
    ConversationLeadContext,
    ConversationMemoryContext,
    ConversationRoutingState,
    ConversationStatus,
    ConversationSummary,
    ConversationSummaryMemory,
    ConversationTurn,
    ConversationWorkingMemory,
    DurableCustomerMemory,
    HumanSupportMessage,
    MemoryFactStatus,
    OwnershipMode,
    QuestionLedgerItem,
    RecommendedProduct,
    ResponseKind,
    SalesMemoryFact,
)
from app.domain.rules import (
    classify_conversation_intent,
    extend_conversation_summary,
    redact_sensitive_text,
)
from app.integrations.postgres.experience import record_exchange_experience
from app.integrations.postgres.models import (
    AgentRunModel,
    AuditEventModel,
    ConversationMemoryModel,
    ConversationModel,
    ConversationSummarySegmentModel,
    CustomerMemoryItemModel,
    HandoffRequestModel,
    MessageModel,
    ResolutionEpisodeModel,
    ToolExecutionModel,
)


class PostgreSQLConversationPersistenceAdapter:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        auto_resolution_hours: int = 24,
    ) -> None:
        self._session_factory = session_factory
        self._auto_resolution_hours = auto_resolution_hours

    async def load_routing_state(
        self,
        *,
        principal: AuthenticatedPrincipal,
        conversation_id: str,
    ) -> ConversationRoutingState | None:
        async with self._session_factory() as session:
            conversation = await session.scalar(
                select(ConversationModel).where(
                    ConversationModel.tenant_id == principal.tenant_id,
                    ConversationModel.conversation_id == conversation_id,
                )
            )
            if conversation is None:
                return None
            _require_conversation_access(conversation, principal)
            handoff_id = await session.scalar(
                select(HandoffRequestModel.handoff_id)
                .where(
                    HandoffRequestModel.tenant_id == principal.tenant_id,
                    HandoffRequestModel.conversation_id == conversation_id,
                    HandoffRequestModel.status.in_(("pending", "assigned")),
                )
                .order_by(HandoffRequestModel.created_at.desc())
                .limit(1)
            )
            return ConversationRoutingState(
                conversation_id=conversation_id,
                status=ConversationStatus(conversation.status),
                ownership_mode=OwnershipMode(conversation.ownership_mode),
                assigned_agent_id=conversation.assigned_agent_id,
                handoff_id=str(handoff_id) if handoff_id else None,
            )

    async def load_human_messages(
        self,
        *,
        principal: AuthenticatedPrincipal,
        conversation_id: str,
        limit: int,
        after_id: int = 0,
    ) -> tuple[HumanSupportMessage, ...]:
        if limit < 1 or limit > 50:
            raise ValueError("human message limit must be between 1 and 50")
        async with self._session_factory() as session:
            conversation = await session.scalar(
                select(ConversationModel).where(
                    ConversationModel.tenant_id == principal.tenant_id,
                    ConversationModel.conversation_id == conversation_id,
                )
            )
            if conversation is None:
                return ()
            _require_conversation_access(conversation, principal)
            statement = select(MessageModel).where(
                MessageModel.tenant_id == principal.tenant_id,
                MessageModel.conversation_id == conversation_id,
                MessageModel.role == "agent",
                MessageModel.message_type == "chat",
            )
            if after_id > 0:
                statement = statement.where(MessageModel.id > after_id).order_by(
                    MessageModel.id.asc()
                )
            else:
                statement = statement.order_by(MessageModel.id.desc())
            messages = list(await session.scalars(statement.limit(limit)))
            if after_id <= 0:
                messages.reverse()
            return tuple(
                HumanSupportMessage(
                    message_id=message.message_id,
                    conversation_id=message.conversation_id,
                    content=message.content,
                    created_at=message.created_at,
                    cursor=message.id,
                )
                for message in messages
            )

    async def load_recent(
        self,
        *,
        principal: AuthenticatedPrincipal,
        conversation_id: str,
        limit: int,
    ) -> tuple[ConversationTurn, ...]:
        if limit < 1 or limit > 20:
            raise ValueError("conversation history limit must be between 1 and 20")
        async with self._session_factory() as session:
            conversation = await session.scalar(
                select(ConversationModel).where(
                    ConversationModel.tenant_id == principal.tenant_id,
                    ConversationModel.conversation_id == conversation_id,
                )
            )
            if conversation is None:
                return ()
            _require_conversation_access(conversation, principal)
            messages = list(
                await session.scalars(
                    select(MessageModel)
                    .where(
                        MessageModel.tenant_id == principal.tenant_id,
                        MessageModel.conversation_id == conversation_id,
                        MessageModel.message_type == "chat",
                    )
                    .order_by(MessageModel.created_at.desc(), MessageModel.id.desc())
                    .limit(limit)
                )
            )
            return tuple(
                ConversationTurn(role=message.role, content=message.content)
                for message in reversed(messages)
            )

    async def persist_exchange(
        self,
        *,
        principal: AuthenticatedPrincipal,
        user_message: str,
        response: AgentResponse,
        request_received_at: datetime | None = None,
        response_sent_at: datetime | None = None,
        request_route: str = "application",
        customer_confirmed_resolution: bool = False,
        visitor_ip_address: str | None = None,
        visitor_country_code: str | None = None,
    ) -> None:
        async with self._session_factory.begin() as session:
            existing_run = await session.scalar(
                select(AgentRunModel).where(
                    AgentRunModel.tenant_id == principal.tenant_id,
                    AgentRunModel.trace_id == response.trace_id,
                )
            )
            if existing_run is not None:
                return

            conversation = await session.scalar(
                select(ConversationModel).where(
                    ConversationModel.tenant_id == principal.tenant_id,
                    ConversationModel.conversation_id == response.conversation_id,
                )
            )
            handoff = None
            if response.handoff_id:
                handoff = await session.scalar(
                    select(HandoffRequestModel).where(
                        HandoffRequestModel.tenant_id == principal.tenant_id,
                        HandoffRequestModel.handoff_id == response.handoff_id,
                    )
                )
            now = _as_utc(response_sent_at or datetime.now(UTC))
            received_at = _as_utc(request_received_at or now)
            latency_ms = max(0, round((now - received_at).total_seconds() * 1000))
            reopened = False
            if conversation is None:
                status = "waiting_human" if response.handoff_id else "open"
                resolution_source = None
                resolved_at = None
                if customer_confirmed_resolution and not response.handoff_id:
                    status = "resolved"
                    resolution_source = "customer_confirmation"
                    resolved_at = now
                session.add(
                    ConversationModel(
                        tenant_id=principal.tenant_id,
                        conversation_id=response.conversation_id,
                        customer_id=(None if principal.is_anonymous else principal.subject_id),
                        visitor_session_id=(
                            principal.visitor_session_id if principal.is_anonymous else None
                        ),
                        channel=(
                            "website_widget"
                            if principal.authentication_method
                            in {
                                "public_widget_token",
                                "widget_site_key",
                                "wordpress_site_key",
                            }
                            else "web_api"
                        ),
                        site_id=principal.site_id or "default-site",
                        visitor_ip_address=visitor_ip_address,
                        visitor_country_code=visitor_country_code,
                        status=status,
                        ownership_mode=("queued" if response.handoff_id else "ai"),
                        queue_id=handoff.queue_id if handoff is not None else None,
                        priority=(handoff.priority if handoff is not None else "normal"),
                        tags=_handoff_tags(handoff),
                        risk_level=int(response.risk_level),
                        unread_count=1,
                        identity_verified=not principal.is_anonymous,
                        last_message_at=now,
                        first_response_at=now,
                        resolved_at=resolved_at,
                        resolution_source=resolution_source,
                        auto_resolution_eligible_at=(
                            now + timedelta(hours=self._auto_resolution_hours)
                            if response.kind is ResponseKind.ANSWER
                            and not response.handoff_id
                            and not customer_confirmed_resolution
                            else None
                        ),
                        created_at=received_at,
                        updated_at=now,
                    )
                )
                await session.flush()
            else:
                _require_conversation_access(conversation, principal)
                conversation.risk_level = max(conversation.risk_level, int(response.risk_level))
                conversation.unread_count += 1
                conversation.last_message_at = now
                conversation.updated_at = now
                if visitor_ip_address is not None:
                    conversation.visitor_ip_address = visitor_ip_address
                if visitor_country_code is not None:
                    conversation.visitor_country_code = visitor_country_code
                if conversation.status == "resolved" and not customer_confirmed_resolution:
                    reopened = True
                    conversation.status = "open"
                    conversation.ownership_mode = "ai"
                    conversation.resolved_at = None
                    conversation.resolution_source = None
                    conversation.reopened_at = received_at
                if conversation.first_response_at is None:
                    conversation.first_response_at = now
                if response.handoff_id and conversation.ownership_mode != "human":
                    conversation.status = "waiting_human"
                    conversation.ownership_mode = "queued"
                    conversation.auto_resolution_eligible_at = None
                    if handoff is not None:
                        conversation.queue_id = handoff.queue_id
                        conversation.priority = handoff.priority
                        conversation.tags = list(
                            dict.fromkeys([*(conversation.tags or []), *_handoff_tags(handoff)])
                        )
                elif customer_confirmed_resolution:
                    conversation.status = "resolved"
                    conversation.ownership_mode = "ai"
                    conversation.resolved_at = now
                    conversation.resolution_source = "customer_confirmation"
                    conversation.auto_resolution_eligible_at = None
                elif response.kind is ResponseKind.ANSWER:
                    conversation.auto_resolution_eligible_at = now + timedelta(
                        hours=self._auto_resolution_hours
                    )

            run_id = response.trace_id
            if (
                customer_confirmed_resolution
                and not principal.is_anonymous
                and conversation is not None
            ):
                previous_messages = list(
                    await session.scalars(
                        select(MessageModel)
                        .where(
                            MessageModel.tenant_id == principal.tenant_id,
                            MessageModel.conversation_id == response.conversation_id,
                            MessageModel.message_type == "chat",
                        )
                        .order_by(MessageModel.id.desc())
                        .limit(2)
                    )
                )
                previous_user = next(
                    (item for item in previous_messages if item.role == "user"), None
                )
                previous_assistant = next(
                    (item for item in previous_messages if item.role == "assistant"), None
                )
                if previous_user is not None and previous_assistant is not None:
                    episode_id = str(
                        uuid5(
                            NAMESPACE_URL,
                            f"{principal.tenant_id}:{response.conversation_id}:customer_confirmation",
                        )
                    )
                    existing_episode = await session.scalar(
                        select(ResolutionEpisodeModel).where(
                            ResolutionEpisodeModel.tenant_id == principal.tenant_id,
                            ResolutionEpisodeModel.episode_id == episode_id,
                        )
                    )
                    if existing_episode is None:
                        session.add(
                            ResolutionEpisodeModel(
                                tenant_id=principal.tenant_id,
                                episode_id=episode_id,
                                customer_id=principal.subject_id,
                                conversation_id=response.conversation_id,
                                intent=classify_conversation_intent(previous_user.content),
                                issue_summary=redact_sensitive_text(previous_user.content)[:2000],
                                product_reference=(
                                    response.memory_state.active_product_sku
                                    if response.memory_state is not None
                                    else None
                                ),
                                order_reference=None,
                                actions_taken=[
                                    execution.tool_name
                                    for execution in response.tool_executions
                                    if execution.status == "succeeded"
                                ],
                                resolution_summary=redact_sensitive_text(
                                    previous_assistant.content
                                )[:3000],
                                resolution_source="customer_confirmation",
                                resolved_at=now,
                                reopened_at=None,
                                confidence=0.95,
                                expires_at=now + timedelta(days=90),
                                status="active",
                            )
                        )
                        session.add(
                            AuditEventModel(
                                tenant_id=principal.tenant_id,
                                event_id=str(uuid4()),
                                event_type="resolution_episode.created",
                                actor_subject_id=principal.subject_id,
                                correlation_id=principal.correlation_id,
                                trace_id=response.trace_id,
                                resource_type="resolution_episode",
                                resource_id=episode_id,
                                details={"conversation_id": response.conversation_id},
                                created_at=now,
                            )
                        )
            session.add_all(
                [
                    MessageModel(
                        tenant_id=principal.tenant_id,
                        message_id=str(uuid5(NAMESPACE_URL, f"{run_id}:user")),
                        conversation_id=response.conversation_id,
                        role="user",
                        content=user_message,
                        redaction_status="completed",
                        message_type="chat",
                        author_subject_id=principal.subject_id,
                        message_metadata={},
                        created_at=now,
                    ),
                    MessageModel(
                        tenant_id=principal.tenant_id,
                        message_id=str(uuid5(NAMESPACE_URL, f"{run_id}:assistant")),
                        conversation_id=response.conversation_id,
                        role="assistant",
                        content=response.message,
                        redaction_status="validated",
                        message_type="chat",
                        author_subject_id=None,
                        message_metadata={
                            "citations": list(response.citations),
                            "related_links": list(response.related_links),
                            "care_procedure_ids": list(response.care_procedure_ids),
                            "care_step_ids": list(response.care_step_ids),
                            "sales_plan": response.sales_plan,
                            "response_brief": response.response_brief,
                            "response_quality": response.response_quality,
                            "turn_plan": response.turn_plan,
                            "planner_model_version": response.planner_model_version,
                            "validation_reason": response.validation_reason,
                            "knowledge_status": response.knowledge_status,
                            "retrieval_degraded": response.retrieval_degraded,
                            "validation_rewrite_count": response.validation_rewrite_count,
                            "model_version": response.model_version,
                            "evidence_ids": list(response.evidence_ids),
                        },
                        created_at=now,
                    ),
                    AgentRunModel(
                        tenant_id=principal.tenant_id,
                        run_id=run_id,
                        trace_id=response.trace_id,
                        conversation_id=response.conversation_id,
                        status="completed",
                        response_kind=response.kind.value,
                        risk_level=int(response.risk_level),
                        handoff_id=response.handoff_id,
                        started_at=now,
                        completed_at=now,
                        request_received_at=received_at,
                        response_sent_at=now,
                        response_latency_ms=latency_ms,
                        request_route=request_route[:120],
                    ),
                    AuditEventModel(
                        tenant_id=principal.tenant_id,
                        event_id=str(uuid4()),
                        event_type="agent.response.persisted",
                        actor_subject_id=principal.subject_id,
                        correlation_id=principal.correlation_id,
                        trace_id=response.trace_id,
                        resource_type="conversation",
                        resource_id=response.conversation_id,
                        details={
                            "response_kind": response.kind.value,
                            "risk_level": int(response.risk_level),
                            "handoff_id": response.handoff_id,
                            "request_route": request_route[:120],
                            "response_latency_ms": latency_ms,
                            "citations": list(response.citations),
                            "related_links": list(response.related_links),
                            "care_procedure_ids": list(response.care_procedure_ids),
                            "care_step_ids": list(response.care_step_ids),
                            "sales_plan": response.sales_plan,
                            "response_brief": response.response_brief,
                            "response_quality": response.response_quality,
                            "turn_plan": response.turn_plan,
                            "planner_model_version": response.planner_model_version,
                            "validation_reason": response.validation_reason,
                            "knowledge_status": response.knowledge_status,
                            "retrieval_degraded": response.retrieval_degraded,
                            "validation_rewrite_count": response.validation_rewrite_count,
                            "model_version": response.model_version,
                            "evidence_ids": list(response.evidence_ids),
                            "sales_policy_version": response.sales_plan.get("policy_version"),
                            "tool_executions": [
                                {
                                    "tool_name": execution.tool_name,
                                    "status": execution.status,
                                    "error_code": execution.error_code,
                                }
                                for execution in response.tool_executions
                            ],
                        },
                        created_at=now,
                    ),
                ]
            )
            await record_exchange_experience(
                session,
                tenant_id=principal.tenant_id,
                site_id=principal.site_id or "default-site",
                conversation_id=response.conversation_id,
                actor_cohort_hash=sha256(
                    (
                        f"{principal.tenant_id}:"
                        f"{principal.visitor_session_id or principal.subject_id}"
                    ).encode()
                ).hexdigest(),
                user_message=user_message,
                response=response,
                occurred_at=now,
                customer_confirmed_resolution=customer_confirmed_resolution,
                reopened=reopened,
            )

            session.add_all(
                [
                    ToolExecutionModel(
                        tenant_id=principal.tenant_id,
                        tool_execution_id=execution.execution_id,
                        run_id=run_id,
                        tool_name=execution.tool_name,
                        status=execution.status,
                        input_summary=execution.input_summary,
                        output_summary=execution.output_summary,
                        error_code=execution.error_code,
                        created_at=execution.created_at or now,
                    )
                    for execution in response.tool_executions
                ]
            )

    async def list_lead_contexts(
        self,
        *,
        tenant_id: str,
        conversation_ids: tuple[str, ...],
    ) -> tuple[ConversationLeadContext, ...]:
        if not conversation_ids:
            return ()
        async with self._session_factory() as session:
            memories = list(
                await session.scalars(
                    select(ConversationMemoryModel).where(
                        ConversationMemoryModel.tenant_id == tenant_id,
                        ConversationMemoryModel.conversation_id.in_(conversation_ids),
                    )
                )
            )
        contexts: list[ConversationLeadContext] = []
        for memory in memories:
            working = memory.working_memory if isinstance(memory.working_memory, dict) else {}
            contexts.append(
                ConversationLeadContext(
                    conversation_id=memory.conversation_id,
                    last_intent=_optional_string(working.get("last_intent")),
                    sales_stage=_optional_string(working.get("sales_stage")),
                    human_requested=bool(working.get("human_requested", False)),
                )
            )
        return tuple(contexts)

    async def authorize_conversation(
        self,
        *,
        principal: AuthenticatedPrincipal,
        conversation_id: str,
    ) -> bool:
        async with self._session_factory() as session:
            conversation = await session.scalar(
                select(ConversationModel).where(
                    ConversationModel.tenant_id == principal.tenant_id,
                    ConversationModel.conversation_id == conversation_id,
                )
            )
            if conversation is None:
                return False
            _require_conversation_access(conversation, principal)
            return True

    async def update_visitor_context(
        self,
        *,
        principal: AuthenticatedPrincipal,
        conversation_id: str,
        ip_address: str | None,
        country_code: str | None,
    ) -> None:
        if ip_address is None and country_code is None:
            return
        async with self._session_factory.begin() as session:
            conversation = await session.scalar(
                select(ConversationModel)
                .where(
                    ConversationModel.tenant_id == principal.tenant_id,
                    ConversationModel.conversation_id == conversation_id,
                )
                .with_for_update()
            )
            if conversation is None:
                return
            _require_conversation_access(conversation, principal)
            changed = False
            if ip_address is not None and conversation.visitor_ip_address != ip_address:
                conversation.visitor_ip_address = ip_address
                changed = True
            if country_code is not None and conversation.visitor_country_code != country_code:
                conversation.visitor_country_code = country_code
                changed = True
            if not changed:
                return
            now = datetime.now(UTC)
            conversation.updated_at = now
            session.add(
                AuditEventModel(
                    tenant_id=principal.tenant_id,
                    event_id=str(uuid4()),
                    event_type="conversation.visitor_context_updated",
                    actor_subject_id=principal.subject_id,
                    correlation_id=principal.correlation_id,
                    trace_id=None,
                    resource_type="conversation",
                    resource_id=conversation_id,
                    details={
                        "ip_address_recorded": conversation.visitor_ip_address is not None,
                        "country_code": conversation.visitor_country_code,
                    },
                    created_at=now,
                )
            )

    async def load(
        self,
        *,
        principal: AuthenticatedPrincipal,
        conversation_id: str,
        query: str = "",
    ) -> ConversationMemoryContext:
        async with self._session_factory() as session:
            conversation = await session.scalar(
                select(ConversationModel).where(
                    ConversationModel.tenant_id == principal.tenant_id,
                    ConversationModel.conversation_id == conversation_id,
                )
            )
            if conversation is None:
                return ConversationMemoryContext(
                    working=ConversationWorkingMemory(
                        response_language=principal.preferred_language or "en"
                    )
                )
            _require_conversation_access(conversation, principal)
            memory = await session.scalar(
                select(ConversationMemoryModel).where(
                    ConversationMemoryModel.tenant_id == principal.tenant_id,
                    ConversationMemoryModel.conversation_id == conversation_id,
                )
            )
            durable_memories: tuple[DurableCustomerMemory, ...] = ()
            if not principal.is_anonymous:
                now = datetime.now(UTC)
                durable = list(
                    await session.scalars(
                        select(CustomerMemoryItemModel)
                        .where(
                            CustomerMemoryItemModel.tenant_id == principal.tenant_id,
                            CustomerMemoryItemModel.customer_id == principal.subject_id,
                            CustomerMemoryItemModel.consent_status == "granted",
                            CustomerMemoryItemModel.status == "active",
                            or_(
                                CustomerMemoryItemModel.expires_at.is_(None),
                                CustomerMemoryItemModel.expires_at > now,
                            ),
                        )
                        .order_by(CustomerMemoryItemModel.updated_at.desc())
                        .limit(20)
                    )
                )
                durable_memories = tuple(
                    DurableCustomerMemory(
                        memory_id=item.memory_id,
                        kind=item.kind,
                        content=item.content,
                    )
                    for item in durable
                )
            if memory is None:
                return ConversationMemoryContext(
                    working=ConversationWorkingMemory(
                        response_language=principal.preferred_language or "en"
                    ),
                    durable_memories=durable_memories,
                )
            if memory.site_id != (principal.site_id or "default-site"):
                raise PermissionError("conversation memory site does not match trusted principal")
            summary = _summary_from_payload(memory.summary, memory.summarized_message_count)
            segments = list(
                await session.scalars(
                    select(ConversationSummarySegmentModel)
                    .where(
                        ConversationSummarySegmentModel.tenant_id == principal.tenant_id,
                        ConversationSummarySegmentModel.conversation_id == conversation_id,
                        ConversationSummarySegmentModel.status == "active",
                    )
                    .order_by(ConversationSummarySegmentModel.last_message_db_id.desc())
                    .limit(30)
                )
            )
            return ConversationMemoryContext(
                working=_working_from_payload(memory.working_memory, memory.revision),
                summary=summary,
                durable_memories=durable_memories,
                summary_memories=_select_summary_memories(summary, segments, query),
            )

    async def save(
        self,
        *,
        principal: AuthenticatedPrincipal,
        conversation_id: str,
        working: ConversationWorkingMemory,
        trace_id: str,
        used_memory_ids: tuple[str, ...] = (),
    ) -> None:
        async with self._session_factory.begin() as session:
            conversation = await session.scalar(
                select(ConversationModel)
                .where(
                    ConversationModel.tenant_id == principal.tenant_id,
                    ConversationModel.conversation_id == conversation_id,
                )
                .with_for_update()
            )
            if conversation is None:
                raise LookupError("conversation not found for memory update")
            _require_conversation_access(conversation, principal)
            model = await session.scalar(
                select(ConversationMemoryModel)
                .where(
                    ConversationMemoryModel.tenant_id == principal.tenant_id,
                    ConversationMemoryModel.conversation_id == conversation_id,
                )
                .with_for_update()
            )
            if model is not None and model.last_trace_id == trace_id:
                return
            summary = (
                _summary_from_payload(model.summary, model.summarized_message_count)
                if model is not None
                else ConversationSummary()
            )
            last_summarized_id = model.last_summarized_message_db_id if model is not None else 0
            summary, last_summarized_id = await _extend_summary(
                session,
                principal.tenant_id,
                conversation_id,
                summary,
                last_summarized_id,
            )
            now = datetime.now(UTC)
            normalized_memory_ids = tuple(
                dict.fromkeys(value.strip() for value in used_memory_ids if value.strip())
            )
            if normalized_memory_ids and not principal.is_anonymous:
                updated_memory_ids = tuple(
                    await session.scalars(
                        update(CustomerMemoryItemModel)
                        .where(
                            CustomerMemoryItemModel.tenant_id == principal.tenant_id,
                            CustomerMemoryItemModel.customer_id == principal.subject_id,
                            CustomerMemoryItemModel.memory_id.in_(normalized_memory_ids),
                            CustomerMemoryItemModel.consent_status == "granted",
                            CustomerMemoryItemModel.status == "active",
                            or_(
                                CustomerMemoryItemModel.expires_at.is_(None),
                                CustomerMemoryItemModel.expires_at > now,
                            ),
                        )
                        .values(
                            last_used_at=now,
                            use_count=CustomerMemoryItemModel.use_count + 1,
                            updated_at=now,
                        )
                        .returning(CustomerMemoryItemModel.memory_id)
                    )
                )
                if updated_memory_ids:
                    session.add(
                        AuditEventModel(
                            tenant_id=principal.tenant_id,
                            event_id=str(
                                uuid5(
                                    NAMESPACE_URL,
                                    f"{principal.tenant_id}:{trace_id}:customer-memory-used",
                                )
                            ),
                            event_type="customer_memory.used",
                            actor_subject_id=principal.subject_id,
                            correlation_id=principal.correlation_id,
                            trace_id=trace_id,
                            resource_type="customer_memory",
                            resource_id=conversation_id,
                            details={"memory_ids": list(updated_memory_ids)},
                            created_at=now,
                        )
                    )
            if model is None:
                model = ConversationMemoryModel(
                    tenant_id=principal.tenant_id,
                    conversation_id=conversation_id,
                    site_id=principal.site_id or "default-site",
                    working_memory=_working_payload(working),
                    summary=_summary_payload(summary),
                    summarized_message_count=summary.summarized_message_count,
                    last_summarized_message_db_id=last_summarized_id,
                    revision=working.revision,
                    last_trace_id=trace_id,
                    created_at=now,
                    updated_at=now,
                )
                session.add(model)
            else:
                if model.site_id != (principal.site_id or "default-site"):
                    raise PermissionError(
                        "conversation memory site does not match trusted principal"
                    )
                model.working_memory = _working_payload(working)
                model.summary = _summary_payload(summary)
                model.summarized_message_count = summary.summarized_message_count
                model.last_summarized_message_db_id = last_summarized_id
                model.revision = working.revision
                model.last_trace_id = trace_id
                model.updated_at = now
            session.add(
                AuditEventModel(
                    tenant_id=principal.tenant_id,
                    event_id=str(
                        uuid5(
                            NAMESPACE_URL,
                            f"{principal.tenant_id}:{trace_id}:conversation-memory",
                        )
                    ),
                    event_type="conversation.memory.updated",
                    actor_subject_id=principal.subject_id,
                    correlation_id=principal.correlation_id,
                    trace_id=trace_id,
                    resource_type="conversation_memory",
                    resource_id=conversation_id,
                    details={
                        "revision": working.revision,
                        "active_product_sku": working.active_product_sku,
                        "pending_product_sku": working.pending_product_sku,
                        "last_intent": working.last_intent,
                        "has_unresolved_question": working.unresolved_question is not None,
                        "summarized_message_count": summary.summarized_message_count,
                    },
                    created_at=now,
                )
            )


def _working_payload(working: ConversationWorkingMemory) -> dict[str, object]:
    return {
        "active_product_sku": working.active_product_sku,
        "pending_product_sku": working.pending_product_sku,
        "candidate_product_skus": list(working.candidate_product_skus),
        "candidate_product_labels": list(working.candidate_product_labels),
        "candidate_products": [
            {
                "sku": item.sku,
                "name": item.name,
                "price": item.price,
                "currency": item.currency,
                "source_url": item.source_url,
                "material": item.material,
                "weight": item.weight,
                "dimensions": item.dimensions,
                "match_reasons": list(item.match_reasons),
                "tradeoffs": list(item.tradeoffs),
                "missing_facts": list(item.missing_facts),
            }
            for item in working.candidate_products
        ],
        "country_code": working.country_code,
        "currency": working.currency,
        "confirmed_fields": list(working.confirmed_fields),
        "missing_fields": list(working.missing_fields),
        "human_requested": working.human_requested,
        "last_intent": working.last_intent,
        "response_language": working.response_language,
        "unresolved_question": working.unresolved_question,
        "primary_goal": working.primary_goal,
        "preference_facts": [
            {
                "key": item.key,
                "value": item.value,
                "status": item.status.value,
                "source_revision": item.source_revision,
            }
            for item in working.preference_facts
        ],
        "objections": list(working.objections),
        "sales_stage": working.sales_stage,
        "next_best_action": working.next_best_action,
        "question_ledger": [
            {
                "key": item.key,
                "text": item.text,
                "status": item.status,
                "asked_revision": item.asked_revision,
            }
            for item in working.question_ledger
        ],
        "recent_response_phrases": list(working.recent_response_phrases),
        "interaction_preferences": list(working.interaction_preferences),
        "revision": working.revision,
    }


def _working_from_payload(payload: dict, revision: int) -> ConversationWorkingMemory:
    return ConversationWorkingMemory(
        active_product_sku=_optional_string(payload.get("active_product_sku")),
        pending_product_sku=_optional_string(payload.get("pending_product_sku")),
        candidate_product_skus=tuple(
            str(value) for value in payload.get("candidate_product_skus", [])
        ),
        candidate_product_labels=tuple(
            str(value) for value in payload.get("candidate_product_labels", [])
        ),
        candidate_products=tuple(
            RecommendedProduct(
                sku=str(item["sku"]),
                name=str(item["name"]),
                price=None if item.get("price") is None else str(item["price"]),
                currency=None if item.get("currency") is None else str(item["currency"]),
                source_url=(None if item.get("source_url") is None else str(item["source_url"])),
                material=None if item.get("material") is None else str(item["material"]),
                weight=None if item.get("weight") is None else str(item["weight"]),
                dimensions=(
                    {str(key): str(value) for key, value in item["dimensions"].items()}
                    if isinstance(item.get("dimensions"), dict)
                    else None
                ),
                match_reasons=tuple(str(value) for value in item.get("match_reasons", [])),
                tradeoffs=tuple(str(value) for value in item.get("tradeoffs", [])),
                missing_facts=tuple(str(value) for value in item.get("missing_facts", [])),
            )
            for item in payload.get("candidate_products", [])
            if isinstance(item, dict) and item.get("sku") and item.get("name")
        ),
        country_code=_optional_string(payload.get("country_code")),
        currency=_optional_string(payload.get("currency")),
        confirmed_fields=tuple(str(value) for value in payload.get("confirmed_fields", [])),
        missing_fields=tuple(str(value) for value in payload.get("missing_fields", [])),
        human_requested=bool(payload.get("human_requested", False)),
        last_intent=_optional_string(payload.get("last_intent")),
        response_language=str(payload.get("response_language") or "en"),
        unresolved_question=_optional_string(payload.get("unresolved_question")),
        primary_goal=_optional_string(payload.get("primary_goal")),
        preference_facts=tuple(
            SalesMemoryFact(
                key=str(item["key"]),
                value=str(item["value"]),
                status=MemoryFactStatus(str(item.get("status") or "confirmed")),
                source_revision=int(item.get("source_revision", 0)),
            )
            for item in payload.get("preference_facts", [])
            if isinstance(item, dict) and item.get("key") and item.get("value")
        ),
        objections=tuple(str(value) for value in payload.get("objections", [])),
        sales_stage=str(payload.get("sales_stage") or "first_contact"),
        next_best_action=str(payload.get("next_best_action") or "answer"),
        question_ledger=tuple(
            QuestionLedgerItem(
                key=str(item["key"]),
                text=str(item.get("text") or ""),
                status=str(item.get("status") or "asked"),
                asked_revision=int(item.get("asked_revision", 0)),
            )
            for item in payload.get("question_ledger", [])
            if isinstance(item, dict) and item.get("key")
        ),
        recent_response_phrases=tuple(
            str(value) for value in payload.get("recent_response_phrases", [])
        ),
        interaction_preferences=tuple(
            str(value) for value in payload.get("interaction_preferences", [])
        ),
        revision=revision,
    )


def _summary_payload(summary: ConversationSummary) -> dict[str, object]:
    return {
        "discussed_product_skus": list(summary.discussed_product_skus),
        "recent_intents": list(summary.recent_intents),
        "user_constraints": list(summary.user_constraints),
        "compact_text": summary.compact_text,
        "summarized_message_count": summary.summarized_message_count,
    }


def _summary_from_payload(payload: dict, count: int) -> ConversationSummary:
    return ConversationSummary(
        discussed_product_skus=tuple(
            str(value) for value in payload.get("discussed_product_skus", [])
        ),
        recent_intents=tuple(str(value) for value in payload.get("recent_intents", [])),
        user_constraints=tuple(str(value) for value in payload.get("user_constraints", [])),
        compact_text=str(payload.get("compact_text") or ""),
        summarized_message_count=int(payload.get("summarized_message_count", count)),
    )


def _optional_string(value: object) -> str | None:
    return str(value) if value not in (None, "") else None


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _handoff_tags(handoff: HandoffRequestModel | None) -> list[str]:
    if handoff is None:
        return []
    tags = ["human-handoff"]
    if handoff.reason_code.startswith("order_"):
        tags.extend(("order", handoff.reason_code.removeprefix("order_").replace("_", "-")))
    return tags


async def _extend_summary(
    session: AsyncSession,
    tenant_id: str,
    conversation_id: str,
    current: ConversationSummary,
    last_summarized_id: int,
) -> tuple[ConversationSummary, int]:
    recent_ids = list(
        await session.scalars(
            select(MessageModel.id)
            .where(
                MessageModel.tenant_id == tenant_id,
                MessageModel.conversation_id == conversation_id,
                MessageModel.message_type == "chat",
            )
            .order_by(MessageModel.id.desc())
            .limit(4)
        )
    )
    if len(recent_ids) < 4:
        return current, last_summarized_id
    oldest_recent_id = min(recent_ids)
    messages = list(
        await session.scalars(
            select(MessageModel)
            .where(
                MessageModel.tenant_id == tenant_id,
                MessageModel.conversation_id == conversation_id,
                MessageModel.message_type == "chat",
                MessageModel.id > last_summarized_id,
                MessageModel.id < oldest_recent_id,
            )
            .order_by(MessageModel.id.asc())
        )
    )
    if not messages:
        return current, last_summarized_id
    complete_message_count = (len(messages) // 4) * 4
    if complete_message_count == 0:
        return current, last_summarized_id
    messages = messages[:complete_message_count]
    summary = extend_conversation_summary(
        current,
        tuple(ConversationTurn(role=item.role, content=item.content) for item in messages),
    )
    for offset in range(0, len(messages), 4):
        group = messages[offset : offset + 4]
        if not group:
            continue
        source = "|".join(f"{item.message_id}:{item.role}:{item.content}" for item in group)
        source_hash = sha256(source.encode()).hexdigest()
        exists = await session.scalar(
            select(ConversationSummarySegmentModel.id).where(
                ConversationSummarySegmentModel.tenant_id == tenant_id,
                ConversationSummarySegmentModel.conversation_id == conversation_id,
                ConversationSummarySegmentModel.source_hash == source_hash,
            )
        )
        if exists is not None:
            continue
        segment_summary = extend_conversation_summary(
            ConversationSummary(),
            tuple(ConversationTurn(role=item.role, content=item.content) for item in group),
        )
        session.add(
            ConversationSummarySegmentModel(
                tenant_id=tenant_id,
                segment_id=str(
                    uuid5(NAMESPACE_URL, f"{tenant_id}:{conversation_id}:{source_hash}")
                ),
                conversation_id=conversation_id,
                site_id=str(
                    await session.scalar(
                        select(ConversationModel.site_id).where(
                            ConversationModel.tenant_id == tenant_id,
                            ConversationModel.conversation_id == conversation_id,
                        )
                    )
                    or "default-site"
                ),
                first_message_db_id=min(item.id for item in group),
                last_message_db_id=max(item.id for item in group),
                source_message_ids=[item.message_id for item in group],
                source_hash=source_hash,
                content=segment_summary.compact_text,
                structured_facts={
                    "discussed_product_skus": list(segment_summary.discussed_product_skus),
                    "recent_intents": list(segment_summary.recent_intents),
                    "user_constraints": list(segment_summary.user_constraints),
                },
                fact_status="customer_stated",
                schema_version=1,
                status="active",
                created_at=datetime.now(UTC),
            )
        )
    return summary, max(item.id for item in messages)


def _select_summary_memories(
    summary: ConversationSummary,
    segments: list[ConversationSummarySegmentModel],
    query: str,
) -> tuple[ConversationSummaryMemory, ...]:
    selected: list[ConversationSummaryMemory] = []
    if summary.compact_text:
        selected.append(
            ConversationSummaryMemory(
                memory_id="conversation-global-summary",
                kind="conversation_global",
                content=summary.compact_text[:1000],
                fact_status="customer_stated",
                source_hash=sha256(summary.compact_text.encode()).hexdigest(),
            )
        )
    query_terms = _summary_terms(query)
    ranked = sorted(
        segments,
        key=lambda item: (
            len(query_terms & _summary_terms(item.content)),
            item.last_message_db_id,
        ),
        reverse=True,
    )
    if ranked and ranked[0].content and ranked[0].content != summary.compact_text:
        item = ranked[0]
        selected.append(
            ConversationSummaryMemory(
                memory_id=item.segment_id,
                kind="conversation_segment",
                content=item.content[:800],
                source_message_ids=tuple(str(value) for value in item.source_message_ids or []),
                fact_status=item.fact_status,
                source_hash=item.source_hash,
                schema_version=item.schema_version,
            )
        )
    return tuple(selected[:2])


def _summary_terms(value: str) -> set[str]:
    import re

    return set(re.findall(r"[a-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", value.casefold()))


def _require_conversation_access(
    conversation: ConversationModel,
    principal: AuthenticatedPrincipal,
) -> None:
    expected_site_id = principal.site_id or "default-site"
    if conversation.site_id != expected_site_id:
        raise PermissionError("conversation site does not match the trusted principal")
    if principal.is_anonymous:
        if conversation.customer_id is not None:
            raise PermissionError("anonymous principal cannot access a customer conversation")
        if principal.authentication_method in {
            "public_presence_token",
            "public_widget_token",
        }:
            if (
                not principal.visitor_session_id
                or conversation.visitor_session_id != principal.visitor_session_id
            ):
                raise PermissionError("visitor session does not match the conversation")
        return
    if conversation.customer_id != principal.subject_id:
        raise PermissionError("conversation customer does not match the trusted principal")
