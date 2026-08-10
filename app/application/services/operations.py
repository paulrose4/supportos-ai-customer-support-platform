import hashlib
import json
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid4, uuid5

from app.application.dto.operations import (
    AddInternalNoteCommand,
    ConversationActionCommand,
    ConversationResult,
    CreateCannedReplyCommand,
    CreateManualHandoffCommand,
    CreateSupportQueueCommand,
    CustomerMemoryResult,
    DeleteCustomerMemoryCommand,
    DeleteResolutionEpisodeCommand,
    GetConversationQuery,
    InboxCountsResult,
    ListCustomerConversationsQuery,
    ListCustomerMemoryQuery,
    ListCustomersQuery,
    ListCustomersResult,
    ListInboxQuery,
    ListInboxResult,
    ListMemoryCandidatesQuery,
    ListResolutionEpisodesQuery,
    ListSitesQuery,
    ListSitesResult,
    ListSupportConfigurationQuery,
    MemoryCandidateResult,
    ProposeMemoryCandidateCommand,
    ResolutionEpisodeResult,
    ReviewMemoryCandidateCommand,
    SendAgentMessageCommand,
    SupportConfigurationResult,
    UpdateConversationRoutingCommand,
    UpdateSupportQueueCommand,
    UpdateSupportQueueMembersCommand,
    UpsertCustomerMemoryCommand,
)
from app.domain.models import (
    AuthenticatedPrincipal,
    ConversationStatus,
    OwnershipMode,
    RealtimeEvent,
    SupportQueue,
    SupportQueueMember,
)
from app.domain.ports import EventPublisherPort, SupportOperationsPort
from app.domain.rules import can_take_over, require_scope, validate_memory


class SupportOperationsService:
    def __init__(
        self,
        operations: SupportOperationsPort,
        event_publisher: EventPublisherPort | None = None,
        memory_candidate_workflow_enabled: bool = True,
    ) -> None:
        self._operations = operations
        self._event_publisher = event_publisher
        self._memory_candidate_workflow_enabled = memory_candidate_workflow_enabled

    async def list_sites(self, query: ListSitesQuery) -> ListSitesResult:
        require_scope(query.principal, "sites:read")
        return ListSitesResult(
            items=tuple(await self._operations.list_sites(tenant_id=query.principal.tenant_id))
        )

    async def list_inbox(self, query: ListInboxQuery) -> ListInboxResult:
        require_scope(query.principal, "support:inbox:read")
        if not 1 <= query.limit <= 100:
            raise ValueError("inbox limit must be between 1 and 100")
        if query.search is not None and len(query.search.strip()) > 200:
            raise ValueError("inbox search is too long")
        assigned_agent_id = query.principal.subject_id if query.mine_only else None
        items = await self._operations.list_inbox(
            tenant_id=query.principal.tenant_id,
            status=query.status,
            ownership_mode=query.ownership_mode,
            assigned_agent_id=assigned_agent_id,
            site_id=query.site_id,
            queue_id=query.queue_id,
            priority=query.priority,
            tag=query.tag,
            unread_only=query.unread_only,
            customer_id=None,
            search=query.search,
            sla_risk_only=query.sla_risk_only,
            priority_risk_only=query.priority_risk_only,
            limit=query.limit,
            cursor=query.cursor,
        )
        page = self._unpack_page(items)
        return ListInboxResult(items=tuple(page[0]), next_cursor=page[1], total=page[2])

    async def inbox_counts(self, query: ListInboxQuery) -> InboxCountsResult:
        require_scope(query.principal, "support:inbox:read")
        counts = await self._operations.inbox_counts(
            tenant_id=query.principal.tenant_id,
            site_id=query.site_id,
            assigned_agent_id=query.principal.subject_id,
        )
        return InboxCountsResult(counts)

    async def list_customers(self, query: ListCustomersQuery) -> ListCustomersResult:
        require_scope(query.principal, "customers:read")
        if not 1 <= query.limit <= 100:
            raise ValueError("customer limit must be between 1 and 100")
        items = await self._operations.list_customers(
            tenant_id=query.principal.tenant_id,
            site_id=query.site_id,
            search=query.search,
            limit=query.limit,
            cursor=query.cursor,
        )
        page = self._unpack_page(items)
        return ListCustomersResult(items=tuple(page[0]), next_cursor=page[1], total=page[2])

    async def list_customer_conversations(
        self, query: ListCustomerConversationsQuery
    ) -> ListInboxResult:
        require_scope(query.principal, "customers:read")
        if not 1 <= query.limit <= 100:
            raise ValueError("conversation limit must be between 1 and 100")
        items = await self._operations.list_inbox(
            tenant_id=query.principal.tenant_id,
            status=None,
            ownership_mode=None,
            assigned_agent_id=None,
            site_id=query.site_id,
            queue_id=None,
            priority=None,
            tag=None,
            unread_only=False,
            customer_id=query.customer_id,
            limit=query.limit,
            cursor=query.cursor,
        )
        page = self._unpack_page(items)
        return ListInboxResult(items=tuple(page[0]), next_cursor=page[1], total=page[2])

    async def get_conversation(self, query: GetConversationQuery) -> ConversationResult:
        require_scope(query.principal, "support:inbox:read")
        workspace = await self._operations.get_workspace(
            tenant_id=query.principal.tenant_id,
            conversation_id=query.conversation_id,
        )
        if workspace is None:
            raise LookupError("conversation not found")
        return ConversationResult(workspace=workspace)

    async def take_over(self, command: ConversationActionCommand) -> ConversationResult:
        self._require_any_scope(command.principal, "support:inbox:takeover", "support:inbox:manage")
        self._validate_idempotency_key(command.idempotency_key)
        current = await self.get_conversation(
            GetConversationQuery(command.principal, command.conversation_id)
        )
        conversation = current.workspace.conversation
        if not self._is_manager(command.principal) and not can_take_over(
            conversation.ownership_mode,
            conversation.assigned_agent_id,
            command.principal.subject_id,
        ):
            raise RuntimeError("conversation is assigned to another agent")
        workspace = await self._operations.transition_conversation(
            tenant_id=command.principal.tenant_id,
            conversation_id=command.conversation_id,
            actor_subject_id=command.principal.subject_id,
            correlation_id=command.principal.correlation_id,
            target_status=ConversationStatus.OPEN,
            target_ownership=OwnershipMode.HUMAN,
            assigned_agent_id=command.principal.subject_id,
            event_type="conversation.taken_over",
            idempotency_key=command.idempotency_key,
            expected_routing_version=conversation.routing_version,
            force=self._is_manager(command.principal),
        )
        await self._publish(
            command.principal, "conversation.taken_over", "conversation", command.conversation_id
        )
        return ConversationResult(workspace)

    async def release_to_ai(self, command: ConversationActionCommand) -> ConversationResult:
        self._require_any_scope(
            command.principal, "support:inbox:resolve:self", "support:inbox:manage"
        )
        self._validate_idempotency_key(command.idempotency_key)
        current = await self.get_conversation(
            GetConversationQuery(command.principal, command.conversation_id)
        )
        workspace = await self._operations.transition_conversation(
            tenant_id=command.principal.tenant_id,
            conversation_id=command.conversation_id,
            actor_subject_id=command.principal.subject_id,
            correlation_id=command.principal.correlation_id,
            target_status=ConversationStatus.OPEN,
            target_ownership=OwnershipMode.AI,
            assigned_agent_id=None,
            event_type="conversation.released_to_ai",
            idempotency_key=command.idempotency_key,
            expected_routing_version=current.workspace.conversation.routing_version,
            force=self._is_manager(command.principal),
        )
        await self._publish(
            command.principal,
            "conversation.released_to_ai",
            "conversation",
            command.conversation_id,
        )
        return ConversationResult(workspace)

    async def resolve(self, command: ConversationActionCommand) -> ConversationResult:
        self._require_any_scope(
            command.principal, "support:inbox:resolve:self", "support:inbox:manage"
        )
        self._validate_idempotency_key(command.idempotency_key)
        current = await self.get_conversation(
            GetConversationQuery(command.principal, command.conversation_id)
        )
        workspace = await self._operations.transition_conversation(
            tenant_id=command.principal.tenant_id,
            conversation_id=command.conversation_id,
            actor_subject_id=command.principal.subject_id,
            correlation_id=command.principal.correlation_id,
            target_status=ConversationStatus.RESOLVED,
            target_ownership=OwnershipMode.HUMAN,
            assigned_agent_id=command.principal.subject_id,
            event_type="conversation.resolved",
            idempotency_key=command.idempotency_key,
            expected_routing_version=current.workspace.conversation.routing_version,
            force=self._is_manager(command.principal),
        )
        await self._publish(
            command.principal, "conversation.resolved", "conversation", command.conversation_id
        )
        return ConversationResult(workspace)

    async def mark_read(self, command: ConversationActionCommand) -> ConversationResult:
        require_scope(command.principal, "support:inbox:read")
        self._validate_idempotency_key(command.idempotency_key)
        current = await self.get_conversation(
            GetConversationQuery(command.principal, command.conversation_id)
        )
        conversation = current.workspace.conversation
        if conversation.unread_count == 0:
            return current
        workspace = await self._operations.transition_conversation(
            tenant_id=command.principal.tenant_id,
            conversation_id=command.conversation_id,
            actor_subject_id=command.principal.subject_id,
            correlation_id=command.principal.correlation_id,
            target_status=conversation.status,
            target_ownership=conversation.ownership_mode,
            assigned_agent_id=conversation.assigned_agent_id,
            event_type="conversation.read",
            idempotency_key=command.idempotency_key,
        )
        await self._publish(
            command.principal, "conversation.read", "conversation", command.conversation_id
        )
        return ConversationResult(workspace)

    async def send_agent_message(self, command: SendAgentMessageCommand) -> ConversationResult:
        self._require_any_scope(
            command.principal, "support:inbox:reply:self", "support:inbox:manage"
        )
        self._validate_idempotency_key(command.idempotency_key)
        content = command.content.strip()
        if not content:
            raise ValueError("message content must not be empty")
        current = await self.get_conversation(
            GetConversationQuery(command.principal, command.conversation_id)
        )
        conversation = current.workspace.conversation
        if (
            conversation.ownership_mode is not OwnershipMode.HUMAN
            or conversation.assigned_agent_id != command.principal.subject_id
        ):
            raise RuntimeError("agent must take over the conversation before replying")
        workspace = await self._operations.append_agent_message(
            tenant_id=command.principal.tenant_id,
            conversation_id=command.conversation_id,
            actor_subject_id=command.principal.subject_id,
            correlation_id=command.principal.correlation_id,
            content=content,
            idempotency_key=command.idempotency_key,
        )
        await self._publish(
            command.principal,
            "conversation.agent_message_sent",
            "conversation",
            command.conversation_id,
        )
        return ConversationResult(workspace)

    async def add_internal_note(self, command: AddInternalNoteCommand) -> ConversationResult:
        self._require_any_scope(
            command.principal, "support:inbox:reply:self", "support:inbox:manage"
        )
        self._validate_idempotency_key(command.idempotency_key)
        content = command.content.strip()
        if not content:
            raise ValueError("internal note must not be empty")
        workspace = await self._operations.append_internal_note(
            tenant_id=command.principal.tenant_id,
            conversation_id=command.conversation_id,
            actor_subject_id=command.principal.subject_id,
            correlation_id=command.principal.correlation_id,
            content=content,
            idempotency_key=command.idempotency_key,
        )
        await self._publish(
            command.principal,
            "conversation.internal_note_added",
            "conversation",
            command.conversation_id,
        )
        return ConversationResult(workspace)

    async def update_routing(self, command: UpdateConversationRoutingCommand) -> ConversationResult:
        self._require_any_scope(command.principal, "support:inbox:routing", "support:inbox:manage")
        self._validate_idempotency_key(command.idempotency_key)
        tags = tuple(sorted({tag.strip().casefold() for tag in command.tags if tag.strip()}))
        if len(tags) > 20 or any(len(tag) > 40 for tag in tags):
            raise ValueError("conversation tags exceed allowed limits")
        current = await self.get_conversation(
            GetConversationQuery(command.principal, command.conversation_id)
        )
        workspace = await self._operations.update_conversation_routing(
            tenant_id=command.principal.tenant_id,
            conversation_id=command.conversation_id,
            actor_subject_id=command.principal.subject_id,
            correlation_id=command.principal.correlation_id,
            assigned_agent_id=command.assigned_agent_id,
            queue_id=command.queue_id,
            priority=command.priority,
            tags=tags,
            idempotency_key=command.idempotency_key,
            expected_routing_version=current.workspace.conversation.routing_version,
            force=self._is_manager(command.principal),
        )
        await self._publish(
            command.principal,
            "conversation.routing_updated",
            "conversation",
            command.conversation_id,
        )
        return ConversationResult(workspace)

    async def create_manual_handoff(
        self, command: CreateManualHandoffCommand
    ) -> ConversationResult:
        self._require_any_scope(command.principal, "support:inbox:takeover", "support:inbox:manage")
        self._validate_idempotency_key(command.idempotency_key)
        summary = command.summary.strip()
        if not summary:
            raise ValueError("handoff summary must not be empty")
        workspace = await self._operations.create_manual_handoff(
            tenant_id=command.principal.tenant_id,
            conversation_id=command.conversation_id,
            actor_subject_id=command.principal.subject_id,
            correlation_id=command.principal.correlation_id,
            summary=summary,
            queue_id=command.queue_id,
            priority=command.priority,
            idempotency_key=command.idempotency_key,
        )
        await self._publish(
            command.principal,
            "handoff.created_manually",
            "conversation",
            command.conversation_id,
        )
        return ConversationResult(workspace)

    async def list_configuration(
        self, query: ListSupportConfigurationQuery
    ) -> SupportConfigurationResult:
        require_scope(query.principal, "support:inbox:read")
        tenant_id = query.principal.tenant_id
        queues = await self._operations.list_queues(tenant_id=tenant_id)
        agents = await self._operations.list_agent_options(tenant_id=tenant_id)
        replies = await self._operations.list_canned_replies(tenant_id=tenant_id)
        return SupportConfigurationResult(tuple(queues), tuple(agents), tuple(replies))

    async def create_queue(self, command: CreateSupportQueueCommand) -> SupportQueue:
        require_scope(command.principal, "support:inbox:manage")
        self._validate_idempotency_key(command.idempotency_key)
        name = command.name.strip()
        description = command.description.strip()
        site_id = command.site_id.strip() if command.site_id else None
        if (
            not name
            or len(name) > 120
            or len(description) > 500
            or (site_id and len(site_id) > 100)
        ):
            raise ValueError("queue name or description is invalid")
        queue_id = str(
            uuid5(NAMESPACE_URL, f"{command.principal.tenant_id}:{command.idempotency_key}:queue")
        )
        return await self._operations.create_queue(
            tenant_id=command.principal.tenant_id,
            queue_id=queue_id,
            name=name,
            description=description,
            is_default=command.is_default,
            site_id=site_id,
            actor_subject_id=command.principal.subject_id,
            correlation_id=command.principal.correlation_id,
            idempotency_key=command.idempotency_key,
        )

    async def update_queue(self, command: UpdateSupportQueueCommand) -> SupportQueue:
        require_scope(command.principal, "support:inbox:manage")
        self._validate_idempotency_key(command.idempotency_key)
        if command.name is not None and (
            not command.name.strip() or len(command.name.strip()) > 120
        ):
            raise ValueError("queue name is invalid")
        if command.description is not None and len(command.description.strip()) > 500:
            raise ValueError("queue description is invalid")
        if command.site_id is not None and not command.site_id.strip():
            raise ValueError("queue site is invalid")
        if command.status not in {None, "active", "disabled"}:
            raise ValueError("queue status is invalid")
        return await self._operations.update_queue(
            tenant_id=command.principal.tenant_id,
            queue_id=command.queue_id.strip(),
            name=command.name.strip() if command.name is not None else None,
            description=command.description.strip() if command.description is not None else None,
            status=command.status,
            is_default=command.is_default,
            site_id=command.site_id.strip() if command.site_id else None,
            actor_subject_id=command.principal.subject_id,
            correlation_id=command.principal.correlation_id,
            idempotency_key=command.idempotency_key,
        )

    async def list_queue_members(
        self, principal: AuthenticatedPrincipal, queue_id: str
    ) -> list[SupportQueueMember]:
        require_scope(principal, "support:inbox:manage")
        return await self._operations.list_queue_members(
            tenant_id=principal.tenant_id, queue_id=queue_id
        )

    async def update_queue_members(
        self, command: UpdateSupportQueueMembersCommand
    ) -> list[SupportQueueMember]:
        require_scope(command.principal, "support:inbox:manage")
        self._validate_idempotency_key(command.idempotency_key)
        agent_ids = tuple(dict.fromkeys(item.strip() for item in command.agent_ids if item.strip()))
        if len(agent_ids) > 200:
            raise ValueError("too many queue members")
        return await self._operations.update_queue_members(
            tenant_id=command.principal.tenant_id,
            queue_id=command.queue_id.strip(),
            agent_ids=agent_ids,
            actor_subject_id=command.principal.subject_id,
            correlation_id=command.principal.correlation_id,
            idempotency_key=command.idempotency_key,
        )

    async def create_canned_reply(
        self, command: CreateCannedReplyCommand
    ) -> SupportConfigurationResult:
        require_scope(command.principal, "support:inbox:write")
        self._validate_idempotency_key(command.idempotency_key)
        title = command.title.strip()
        content = command.content.strip()
        shortcut = command.shortcut.strip().casefold().removeprefix("/")
        if not title or not content or not shortcut:
            raise ValueError("canned reply fields must not be empty")
        if len(title) > 160 or len(content) > 5000 or len(shortcut) > 80:
            raise ValueError("canned reply exceeds allowed limits")
        reply_id = str(
            uuid5(
                NAMESPACE_URL,
                f"{command.principal.tenant_id}:{command.idempotency_key}:canned-reply",
            )
        )
        reply = await self._operations.create_canned_reply(
            tenant_id=command.principal.tenant_id,
            actor_subject_id=command.principal.subject_id,
            correlation_id=command.principal.correlation_id,
            reply_id=reply_id,
            title=title,
            content=content,
            shortcut=shortcut,
            idempotency_key=command.idempotency_key,
        )
        await self._publish(
            command.principal,
            "canned_reply.created",
            "canned_reply",
            reply.reply_id,
        )
        return SupportConfigurationResult((), (), (reply,))

    async def list_memory(self, query: ListCustomerMemoryQuery) -> CustomerMemoryResult:
        require_scope(query.principal, "customers:memory:read")
        items = await self._operations.list_customer_memory(
            tenant_id=query.principal.tenant_id,
            customer_id=query.customer_id,
        )
        return CustomerMemoryResult(tuple(items))

    async def upsert_memory(self, command: UpsertCustomerMemoryCommand) -> CustomerMemoryResult:
        require_scope(command.principal, "customers:memory:write")
        self._validate_idempotency_key(command.idempotency_key)
        validate_memory(
            kind=command.kind,
            content=command.content,
            source_type=command.source_type,
            confidence=command.confidence,
            consent_status=command.consent_status,
        )
        memory_id = str(
            uuid5(
                NAMESPACE_URL,
                ":".join(
                    (
                        command.principal.tenant_id,
                        command.customer_id,
                        command.idempotency_key,
                    )
                ),
            )
        )
        item = await self._operations.upsert_customer_memory(
            tenant_id=command.principal.tenant_id,
            customer_id=command.customer_id,
            actor_subject_id=command.principal.subject_id,
            correlation_id=command.principal.correlation_id,
            memory_id=memory_id,
            kind=command.kind,
            content=command.content.strip(),
            source_type=command.source_type,
            source_id=command.source_id,
            confidence=command.confidence,
            consent_status=command.consent_status,
            idempotency_key=command.idempotency_key,
        )
        await self._publish(
            command.principal,
            "customer_memory.upserted",
            "customer_memory",
            item.memory_id,
            {"customer_id": command.customer_id, "kind": item.kind.value},
        )
        return CustomerMemoryResult((item,))

    async def delete_memory(self, command: DeleteCustomerMemoryCommand) -> bool:
        require_scope(command.principal, "customers:memory:write")
        self._validate_idempotency_key(command.idempotency_key)
        deleted = await self._operations.delete_customer_memory(
            tenant_id=command.principal.tenant_id,
            customer_id=command.customer_id,
            memory_id=command.memory_id,
            actor_subject_id=command.principal.subject_id,
            correlation_id=command.principal.correlation_id,
            idempotency_key=command.idempotency_key,
        )
        await self._publish(
            command.principal,
            "customer_memory.deleted",
            "customer_memory",
            command.memory_id,
            {"customer_id": command.customer_id},
        )
        return deleted

    async def list_memory_candidates(
        self, query: ListMemoryCandidatesQuery
    ) -> MemoryCandidateResult:
        self._require_memory_candidate_workflow()
        require_scope(query.principal, "customers:memory:read")
        items = await self._operations.list_memory_candidates(
            tenant_id=query.principal.tenant_id,
            customer_id=query.customer_id,
        )
        return MemoryCandidateResult(tuple(items))

    async def propose_memory_candidate(
        self, command: ProposeMemoryCandidateCommand
    ) -> MemoryCandidateResult:
        self._require_memory_candidate_workflow()
        require_scope(command.principal, "customers:memory:write")
        self._validate_idempotency_key(command.idempotency_key)
        _validate_candidate_payload(command.normalized_value, command.sensitivity)
        if command.sensitivity == "sensitive" and not command.consent_required:
            raise ValueError("sensitive memory candidates require explicit consent")
        validate_memory(
            kind=command.kind,
            content=command.display_text,
            source_type="agent",
            confidence=command.confidence,
            consent_status="granted",
        )
        canonical = json.dumps(
            {
                "kind": command.kind.value,
                "value": command.normalized_value,
                "conversation_id": command.conversation_id,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        candidate_id = str(
            uuid5(
                NAMESPACE_URL,
                f"{command.principal.tenant_id}:{command.customer_id}:{fingerprint}",
            )
        )
        item = await self._operations.propose_memory_candidate(
            tenant_id=command.principal.tenant_id,
            customer_id=command.customer_id,
            conversation_id=command.conversation_id,
            actor_subject_id=command.principal.subject_id,
            correlation_id=command.principal.correlation_id,
            candidate_id=candidate_id,
            candidate_fingerprint=fingerprint,
            kind=command.kind,
            normalized_value=command.normalized_value,
            display_text=command.display_text,
            source_message_ids=command.source_message_ids,
            source_trace_id=command.source_trace_id,
            confidence=command.confidence,
            sensitivity=command.sensitivity,
            consent_required=command.consent_required,
            idempotency_key=command.idempotency_key,
        )
        return MemoryCandidateResult((item,))

    async def review_memory_candidate(
        self, command: ReviewMemoryCandidateCommand
    ) -> MemoryCandidateResult:
        self._require_memory_candidate_workflow()
        require_scope(command.principal, "customers:memory:write")
        self._validate_idempotency_key(command.idempotency_key)
        if not command.approve and not str(command.rejection_reason or "").strip():
            raise ValueError("candidate rejection requires a reason")
        candidate, memory = await self._operations.review_memory_candidate(
            tenant_id=command.principal.tenant_id,
            customer_id=command.customer_id,
            candidate_id=command.candidate_id,
            actor_subject_id=command.principal.subject_id,
            correlation_id=command.principal.correlation_id,
            approve=command.approve,
            rejection_reason=command.rejection_reason,
            idempotency_key=command.idempotency_key,
        )
        return MemoryCandidateResult((candidate,), memory)

    async def list_resolution_episodes(
        self, query: ListResolutionEpisodesQuery
    ) -> ResolutionEpisodeResult:
        require_scope(query.principal, "customers:memory:read")
        items = await self._operations.list_resolution_episodes(
            tenant_id=query.principal.tenant_id,
            customer_id=query.customer_id,
        )
        return ResolutionEpisodeResult(tuple(items))

    async def delete_resolution_episode(self, command: DeleteResolutionEpisodeCommand) -> bool:
        require_scope(command.principal, "customers:memory:write")
        self._validate_idempotency_key(command.idempotency_key)
        return await self._operations.delete_resolution_episode(
            tenant_id=command.principal.tenant_id,
            customer_id=command.customer_id,
            episode_id=command.episode_id,
            actor_subject_id=command.principal.subject_id,
            correlation_id=command.principal.correlation_id,
            idempotency_key=command.idempotency_key,
        )

    async def _publish(
        self,
        principal: AuthenticatedPrincipal,
        event_type: str,
        resource_type: str,
        resource_id: str,
        payload: dict | None = None,
    ) -> None:
        if self._event_publisher is None:
            return
        await self._event_publisher.publish(
            RealtimeEvent(
                event_id=str(uuid4()),
                tenant_id=principal.tenant_id,
                event_type=event_type,
                resource_type=resource_type,
                resource_id=resource_id,
                payload=payload or {},
                occurred_at=datetime.now(UTC),
            )
        )

    @staticmethod
    def _validate_idempotency_key(value: str) -> None:
        if not 8 <= len(value.strip()) <= 200:
            raise ValueError("idempotency key must be between 8 and 200 characters")

    @staticmethod
    def _is_manager(principal: AuthenticatedPrincipal) -> bool:
        return "support:inbox:manage" in principal.scopes

    @staticmethod
    def _require_any_scope(principal: AuthenticatedPrincipal, *scopes: str) -> None:
        if not set(scopes).intersection(principal.scopes):
            raise PermissionError(f"missing one of required scopes: {', '.join(scopes)}")

    @staticmethod
    def _unpack_page(value):  # type: ignore[no-untyped-def]
        if isinstance(value, tuple) and len(value) == 3:
            return value
        items = list(value)
        return items, None, len(items)

    def _require_memory_candidate_workflow(self) -> None:
        if not self._memory_candidate_workflow_enabled:
            raise RuntimeError("memory candidate workflow is disabled")


def _validate_candidate_payload(value: dict[str, object], sensitivity: str) -> None:
    allowed_keys = {
        "preferred_language",
        "currency",
        "preferred_material",
        "preferred_size",
        "preferred_weight",
        "care_resolution",
        "product_sku",
    }
    if not value or not set(value).issubset(allowed_keys):
        raise ValueError("memory candidate contains unsupported structured fields")
    if sensitivity not in {"standard", "sensitive"}:
        raise ValueError("memory candidate sensitivity is unsupported")
    for item in value.values():
        if not isinstance(item, str) or not item.strip() or len(item) > 500:
            raise ValueError("memory candidate values must be bounded non-empty strings")
