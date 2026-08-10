import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from app.application.dto import (
    GetPublicConversationStateQuery,
    HandleChatCommand,
    HandleChatResult,
    ListHumanMessagesQuery,
    ListHumanMessagesResult,
    PublicConversationStateResult,
)
from app.application.visitor_context import (
    normalize_visitor_country_code,
    normalize_visitor_ip_address,
)
from app.domain.models import (
    AgentResponse,
    AuthenticatedPrincipal,
    ConversationMemoryContext,
    ConversationWorkingMemory,
    RealtimeEvent,
    ResponseKind,
    RiskLevel,
)
from app.domain.ports import (
    AgentRunnerPort,
    ConversationContextPort,
    ConversationMemoryPort,
    ConversationPersistencePort,
    EventPublisherPort,
    PublicWidgetCursorPort,
    RequestMetricsPort,
)
from app.domain.rules import (
    is_resolution_confirmation,
    redact_sensitive_text,
    resolve_response_language,
)


class HandleChatService:
    def __init__(
        self,
        agent_runner: AgentRunnerPort,
        conversation_persistence: ConversationPersistencePort,
        event_publisher: EventPublisherPort | None = None,
        conversation_context: ConversationContextPort | None = None,
        conversation_memory: ConversationMemoryPort | None = None,
        experience_metrics: RequestMetricsPort | None = None,
        message_cursors: PublicWidgetCursorPort | None = None,
    ) -> None:
        self._agent_runner = agent_runner
        self._conversation_persistence = conversation_persistence
        self._event_publisher = event_publisher
        self._conversation_context = conversation_context
        self._conversation_memory = conversation_memory
        self._experience_metrics = experience_metrics
        self._message_cursors = message_cursors

    async def list_human_messages(
        self,
        query: ListHumanMessagesQuery,
    ) -> ListHumanMessagesResult:
        conversation_id = query.conversation_id.strip()
        if not conversation_id or len(conversation_id) > 100:
            raise ValueError("conversation_id must be a bounded opaque identifier")
        if not 1 <= query.limit <= 50:
            raise ValueError("human message limit must be between 1 and 50")
        if self._conversation_context is None:
            return ListHumanMessagesResult(())
        routing = await self._conversation_context.load_routing_state(
            principal=query.principal, conversation_id=conversation_id
        )
        if routing is None:
            return ListHumanMessagesResult((), None, False, query.after_cursor)
        if not routing.requires_human and routing.status.value != "resolved":
            return ListHumanMessagesResult((), routing.status.value, False, query.after_cursor)
        after_id = self._verify_message_cursor(query, conversation_id)
        items = await self._conversation_context.load_human_messages(
            principal=query.principal,
            conversation_id=conversation_id,
            limit=query.limit,
            after_id=after_id,
        )
        next_cursor = query.after_cursor
        if items and self._message_cursors is not None:
            next_cursor = self._issue_message_cursor(
                query.principal, conversation_id, items[-1].cursor
            )
        return ListHumanMessagesResult(
            items, routing.status.value, routing.requires_human, next_cursor
        )

    async def get_public_conversation_state(
        self,
        query: GetPublicConversationStateQuery,
    ) -> PublicConversationStateResult:
        conversation_id = query.conversation_id.strip()
        if not conversation_id or len(conversation_id) > 100:
            raise ValueError("conversation_id must be a bounded opaque identifier")
        if self._conversation_context is None:
            return PublicConversationStateResult(False)
        routing = await self._conversation_context.load_routing_state(
            principal=query.principal,
            conversation_id=conversation_id,
        )
        if routing is None:
            return PublicConversationStateResult(False)
        last_cursor = None
        if routing.requires_human and self._message_cursors is not None:
            latest = await self._conversation_context.load_human_messages(
                principal=query.principal,
                conversation_id=conversation_id,
                limit=1,
            )
            if latest:
                last_cursor = self._issue_message_cursor(
                    query.principal, conversation_id, latest[-1].cursor
                )
        return PublicConversationStateResult(
            exists=True,
            conversation_status=routing.status.value,
            handoff_active=routing.requires_human,
            handoff_id=routing.handoff_id,
            last_human_cursor=last_cursor,
        )

    def _verify_message_cursor(
        self,
        query: ListHumanMessagesQuery,
        conversation_id: str,
    ) -> int:
        if not query.after_cursor:
            return 0
        if self._message_cursors is None:
            raise ValueError("message cursors are unavailable")
        principal = query.principal
        if not principal.site_id or not principal.visitor_session_id:
            raise PermissionError("public widget cursor identity is unavailable")
        return self._message_cursors.verify(
            cursor=query.after_cursor,
            tenant_id=principal.tenant_id,
            site_id=principal.site_id,
            session_id=principal.visitor_session_id,
            conversation_id=conversation_id,
        )

    def _issue_message_cursor(
        self,
        principal: AuthenticatedPrincipal,
        conversation_id: str,
        after_id: int,
    ) -> str:
        if (
            self._message_cursors is None
            or not principal.site_id
            or not principal.visitor_session_id
        ):
            raise PermissionError("public widget cursor identity is unavailable")
        return self._message_cursors.issue(
            tenant_id=principal.tenant_id,
            site_id=principal.site_id,
            session_id=principal.visitor_session_id,
            conversation_id=conversation_id,
            after_id=after_id,
        )

    async def execute(self, command: HandleChatCommand) -> HandleChatResult:
        request_received_at = command.request_received_at or datetime.now(UTC)
        message = command.message.strip()
        if not message:
            raise ValueError("message must not be empty")
        page_path = command.page_path.strip() or "/"
        if not page_path.startswith("/") or page_path.startswith("//") or len(page_path) > 500:
            raise ValueError("page_path must be a bounded relative path")
        visitor_ip_address = normalize_visitor_ip_address(command.visitor_ip_address)
        visitor_country_code = normalize_visitor_country_code(command.visitor_country_code)

        conversation_id = command.conversation_id or str(uuid4())
        routing = None
        if command.conversation_id and self._conversation_context is not None:
            routing = await self._conversation_context.load_routing_state(
                principal=command.principal,
                conversation_id=conversation_id,
            )
            # A public visitor cannot choose an arbitrary identifier for a new
            # conversation. Unknown IDs are replaced with a server-generated ID;
            # existing IDs are still checked by the conversation access port.
            if (
                routing is None
                and command.principal.authentication_method == "public_widget_token"
                and command.principal.visitor_session_id
            ):
                conversation_id = str(uuid4())
        if routing is not None and routing.requires_human:
            response = AgentResponse(
                conversation_id=conversation_id,
                message=_human_queue_message(
                    resolve_response_language(
                        message,
                        (),
                        command.principal.preferred_language,
                    )
                ),
                kind=ResponseKind.HANDOFF,
                risk_level=RiskLevel.PUBLIC,
                trace_id=str(uuid4()),
                handoff_id=routing.handoff_id,
            )
            await self._conversation_persistence.persist_exchange(
                principal=command.principal,
                user_message=redact_sensitive_text(message),
                response=response,
                request_received_at=request_received_at,
                response_sent_at=datetime.now(UTC),
                request_route=command.request_route,
                visitor_ip_address=visitor_ip_address,
                visitor_country_code=visitor_country_code,
            )
            await self._publish_event(command, response, "conversation.human_followup_received")
            return HandleChatResult(response=response)

        if command.conversation_id and is_resolution_confirmation(message):
            response = AgentResponse(
                conversation_id=conversation_id,
                message=_resolution_confirmation_message(
                    resolve_response_language(
                        message,
                        (),
                        command.principal.preferred_language,
                    )
                ),
                kind=ResponseKind.ANSWER,
                risk_level=RiskLevel.PUBLIC,
                trace_id=str(uuid4()),
            )
            await self._conversation_persistence.persist_exchange(
                principal=command.principal,
                user_message=redact_sensitive_text(message),
                response=response,
                request_received_at=request_received_at,
                response_sent_at=datetime.now(UTC),
                request_route=command.request_route,
                customer_confirmed_resolution=True,
                visitor_ip_address=visitor_ip_address,
                visitor_country_code=visitor_country_code,
            )
            await self._publish_event(command, response, "conversation.customer_confirmed_resolved")
            return HandleChatResult(response=response)

        history = ()
        memory = ConversationMemoryContext(
            working=ConversationWorkingMemory(
                response_language=command.principal.preferred_language or "en"
            )
        )
        if (
            command.conversation_id
            and self._conversation_context is not None
            and self._conversation_memory is not None
        ):
            try:
                history, memory = await asyncio.gather(
                    self._conversation_context.load_recent(
                        principal=command.principal,
                        conversation_id=conversation_id,
                        limit=4,
                    ),
                    self._conversation_memory.load(
                        principal=command.principal,
                        conversation_id=conversation_id,
                        query=message,
                    ),
                )
            except PermissionError as exc:
                raise ValueError("conversation is not available") from exc
        elif command.conversation_id and self._conversation_context is not None:
            try:
                history = await self._conversation_context.load_recent(
                    principal=command.principal,
                    conversation_id=conversation_id,
                    limit=4,
                )
            except PermissionError as exc:
                raise ValueError("conversation is not available") from exc
        elif command.conversation_id and self._conversation_memory is not None:
            memory = await self._conversation_memory.load(
                principal=command.principal,
                conversation_id=conversation_id,
                query=message,
            )
        response = await self._agent_runner.run(
            conversation_id=conversation_id,
            message=message,
            principal=command.principal,
            history=history,
            page_path=page_path,
            memory=memory,
        )
        await self._conversation_persistence.persist_exchange(
            principal=command.principal,
            user_message=redact_sensitive_text(message),
            response=response,
            request_received_at=request_received_at,
            response_sent_at=datetime.now(UTC),
            request_route=command.request_route,
            customer_confirmed_resolution=(
                command.conversation_id is not None and is_resolution_confirmation(message)
            ),
            visitor_ip_address=visitor_ip_address,
            visitor_country_code=visitor_country_code,
        )
        if self._conversation_memory is not None and response.memory_state is not None:
            await self._conversation_memory.save(
                principal=command.principal,
                conversation_id=response.conversation_id,
                working=response.memory_state,
                trace_id=response.trace_id,
                used_memory_ids=response.used_memory_ids,
            )
        await self._publish_event(command, response, "conversation.ai_exchange_persisted")
        self._record_experience(response)
        return HandleChatResult(response=response)

    def _record_experience(self, response: AgentResponse) -> None:
        if self._experience_metrics is None:
            return
        quality = response.response_quality
        self._experience_metrics.record_agent_response(
            dialogue_act=str(response.response_brief.get("dialogue_act") or "unknown"),
            response_kind=response.kind.value,
            generation_count=int(quality.get("generation_count") or 0),
            repaired=bool(quality.get("repaired_issue_codes")),
            fallback=bool(quality.get("fallback", False)),
        )

    async def _publish_event(
        self,
        command: HandleChatCommand,
        response: AgentResponse,
        event_type: str,
    ) -> None:
        if self._event_publisher is None:
            return
        await self._event_publisher.publish(
            RealtimeEvent(
                event_id=str(uuid4()),
                tenant_id=command.principal.tenant_id,
                event_type=event_type,
                resource_type="conversation",
                resource_id=response.conversation_id,
                payload={
                    "kind": response.kind.value,
                    "risk_level": int(response.risk_level),
                    "handoff_id": response.handoff_id,
                },
                occurred_at=datetime.now(UTC),
            )
        )


def _human_queue_message(language: str | None) -> str:
    base = (language or "en").replace("_", "-").casefold().split("-", 1)[0]
    messages = {
        "zh": "已收到您的补充消息，人工客服正在处理当前会话。我们会在确认后尽快回复您。",
        "ja": (
            "追加のメッセージを受け付けました。担当者が確認中です。確認でき次第、返信いたします。"
        ),
        "es": (
            "Hemos recibido tu mensaje. Un agente está revisando la conversación y te responderá "
            "lo antes posible."
        ),
        "de": (
            "Ihre Nachricht ist eingegangen. Ein Mitarbeiter prüft das Gespräch und antwortet "
            "so bald wie möglich."
        ),
        "fr": (
            "Votre message a bien été reçu. Un agent examine la conversation et vous répondra "
            "dès que possible."
        ),
    }
    return messages.get(
        base,
        (
            "Your message has been received. A human agent is reviewing this conversation and "
            "will reply as soon as possible."
        ),
    )


def _resolution_confirmation_message(language: str | None) -> str:
    base = (language or "en").replace("_", "-").casefold().split("-", 1)[0]
    messages = {
        "zh": "好的，这次咨询已标记为解决。后续有新问题可以随时继续联系我们。",
        "ja": "承知しました。このお問い合わせは解決済みにしました。",
        "es": "Entendido. He marcado esta consulta como resuelta.",
        "de": "Verstanden. Ich habe diese Anfrage als gelöst markiert.",
        "fr": "Compris. J’ai marqué cette demande comme résolue.",
    }
    return messages.get(base, "Understood. I have marked this conversation as resolved.")
