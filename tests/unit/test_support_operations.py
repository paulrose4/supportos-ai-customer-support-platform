from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.application.dto import (
    AddInternalNoteCommand,
    ConversationActionCommand,
    ListInboxQuery,
    ListMemoryCandidatesQuery,
    SendAgentMessageCommand,
    UpdateConversationRoutingCommand,
    UpdateSupportQueueMembersCommand,
    UpsertCustomerMemoryCommand,
)
from app.application.dto.operations import ListCustomersQuery
from app.application.services import SupportOperationsService
from app.domain.models import (
    AuthenticatedPrincipal,
    ConversationPriority,
    ConversationStatus,
    ConversationWorkspace,
    CustomerDirectoryItem,
    CustomerMemoryItem,
    InboxConversation,
    MemoryKind,
    OwnershipMode,
    SupportMessage,
    SupportQueueMember,
)


class InMemorySupportOperations:
    def __init__(self) -> None:
        now = datetime.now(UTC)
        self.workspace = ConversationWorkspace(
            conversation=InboxConversation(
                conversation_id="conversation-1",
                tenant_id="tenant-a",
                site_id="site-a",
                customer_id="customer-1",
                customer_display_name="Customer",
                channel="wordpress_widget",
                status=ConversationStatus.WAITING_HUMAN,
                ownership_mode=OwnershipMode.QUEUED,
                assigned_agent_id=None,
                risk_level=2,
                unread_count=1,
                identity_verified=True,
                last_message_preview="help",
                last_message_at=now,
                updated_at=now,
            ),
            messages=(),
        )
        self.memories: dict[str, CustomerMemoryItem] = {}
        self.transition_calls = 0
        self.queue_members: tuple[str, ...] = ()

    async def list_sites(self, *, tenant_id: str):  # type: ignore[no-untyped-def]
        return []

    async def list_inbox(self, **values):  # type: ignore[no-untyped-def]
        assert values["tenant_id"] == "tenant-a"
        return [self.workspace.conversation]

    async def list_customers(self, **values):  # type: ignore[no-untyped-def]
        assert values["tenant_id"] == "tenant-a"
        return [
            CustomerDirectoryItem(
                customer_id="customer-1",
                tenant_id="tenant-a",
                display_name="Customer",
                conversation_count=3,
                last_conversation_at=datetime.now(UTC),
            )
        ]

    async def get_workspace(self, *, tenant_id: str, conversation_id: str):  # type: ignore[no-untyped-def]
        if tenant_id == "tenant-a" and conversation_id == "conversation-1":
            return self.workspace
        return None

    async def transition_conversation(self, **values):  # type: ignore[no-untyped-def]
        self.transition_calls += 1
        self.workspace = replace(
            self.workspace,
            conversation=replace(
                self.workspace.conversation,
                status=values["target_status"],
                ownership_mode=values["target_ownership"],
                assigned_agent_id=values["assigned_agent_id"],
                unread_count=0,
            ),
        )
        return self.workspace

    async def append_agent_message(self, **values):  # type: ignore[no-untyped-def]
        message = SupportMessage(
            message_id=values["idempotency_key"],
            conversation_id=values["conversation_id"],
            role="agent",
            content=values["content"],
            message_type="chat",
            author_subject_id=values["actor_subject_id"],
            created_at=datetime.now(UTC),
        )
        self.workspace = replace(self.workspace, messages=(*self.workspace.messages, message))
        return self.workspace

    async def append_internal_note(self, **values):  # type: ignore[no-untyped-def]
        message = SupportMessage(
            message_id=values["idempotency_key"],
            conversation_id=values["conversation_id"],
            role="agent",
            content=values["content"],
            message_type="internal_note",
            author_subject_id=values["actor_subject_id"],
            metadata={"visibility": "operators_only"},
            created_at=datetime.now(UTC),
        )
        self.workspace = replace(self.workspace, messages=(*self.workspace.messages, message))
        return self.workspace

    async def update_conversation_routing(self, **values):  # type: ignore[no-untyped-def]
        self.workspace = replace(
            self.workspace,
            conversation=replace(
                self.workspace.conversation,
                assigned_agent_id=values["assigned_agent_id"],
                queue_id=values["queue_id"],
                priority=values["priority"],
                tags=values["tags"],
                ownership_mode=OwnershipMode.HUMAN,
            ),
        )
        return self.workspace

    async def list_customer_memory(self, **values):  # type: ignore[no-untyped-def]
        return list(self.memories.values())

    async def upsert_customer_memory(self, **values):  # type: ignore[no-untyped-def]
        now = datetime.now(UTC)
        item = CustomerMemoryItem(
            memory_id=values["memory_id"],
            tenant_id=values["tenant_id"],
            customer_id=values["customer_id"],
            kind=values["kind"],
            content=values["content"],
            source_type=values["source_type"],
            source_id=values["source_id"],
            confidence=values["confidence"],
            consent_status=values["consent_status"],
            expires_at=None,
            created_at=now,
            updated_at=now,
        )
        self.memories[item.memory_id] = item
        return item

    async def delete_customer_memory(self, **values):  # type: ignore[no-untyped-def]
        self.memories.pop(values["memory_id"], None)
        return True

    async def update_queue_members(self, **values):  # type: ignore[no-untyped-def]
        self.queue_members = values["agent_ids"]
        return [SupportQueueMember(item, item, "member", "active") for item in self.queue_members]


def principal(*, scopes: frozenset[str] | None = None) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        subject_id="agent-1",
        tenant_id="tenant-a",
        roles=frozenset({"support_agent"}),
        scopes=scopes
        or frozenset(
            {
                "support:inbox:read",
                "support:inbox:write",
                "support:inbox:reply:self",
                "support:inbox:takeover",
                "support:inbox:routing",
                "support:inbox:resolve:self",
                "customers:memory:read",
                "customers:memory:write",
                "customers:read",
            }
        ),
        authentication_method="mock",
        authenticated_at=datetime.now(UTC),
        correlation_id="correlation-1",
    )


@pytest.mark.asyncio
async def test_agent_can_take_over_and_reply_only_after_ownership() -> None:
    adapter = InMemorySupportOperations()
    service = SupportOperationsService(adapter)

    takeover = await service.take_over(
        ConversationActionCommand(principal(), "conversation-1", "takeover-key-1")
    )
    reply = await service.send_agent_message(
        SendAgentMessageCommand(
            principal(), "conversation-1", "We are checking this.", "message-key-1"
        )
    )

    assert takeover.workspace.conversation.ownership_mode is OwnershipMode.HUMAN
    assert takeover.workspace.conversation.assigned_agent_id == "agent-1"
    assert reply.workspace.messages[-1].role == "agent"


@pytest.mark.asyncio
async def test_inbox_requires_tenant_scoped_read_permission() -> None:
    service = SupportOperationsService(InMemorySupportOperations())

    with pytest.raises(PermissionError):
        await service.list_inbox(
            ListInboxQuery(principal(scopes=frozenset({"support:inbox:write"})))
        )


@pytest.mark.asyncio
async def test_customer_directory_uses_its_own_port_and_scope() -> None:
    service = SupportOperationsService(InMemorySupportOperations())

    result = await service.list_customers(ListCustomersQuery(principal()))

    assert result.items[0].customer_id == "customer-1"
    assert result.items[0].conversation_count == 3
    with pytest.raises(PermissionError):
        await service.list_customers(
            ListCustomersQuery(principal(scopes=frozenset({"support:inbox:read"})))
        )


@pytest.mark.asyncio
async def test_memory_candidate_workflow_can_be_disabled_without_touching_storage() -> None:
    service = SupportOperationsService(
        InMemorySupportOperations(), memory_candidate_workflow_enabled=False
    )

    with pytest.raises(RuntimeError, match="memory candidate workflow is disabled"):
        await service.list_memory_candidates(ListMemoryCandidatesQuery(principal(), "customer-1"))


@pytest.mark.asyncio
async def test_internal_note_is_operator_only_message() -> None:
    service = SupportOperationsService(InMemorySupportOperations())

    result = await service.add_internal_note(
        AddInternalNoteCommand(
            principal=principal(),
            conversation_id="conversation-1",
            content="Check the policy before replying",
            idempotency_key="internal-note-key-1",
        )
    )

    note = result.workspace.messages[-1]
    assert note.message_type == "internal_note"
    assert note.metadata["visibility"] == "operators_only"


@pytest.mark.asyncio
async def test_routing_update_normalizes_tags_and_assignment() -> None:
    service = SupportOperationsService(InMemorySupportOperations())

    result = await service.update_routing(
        UpdateConversationRoutingCommand(
            principal=principal(),
            conversation_id="conversation-1",
            assigned_agent_id="agent-2",
            queue_id="general",
            priority=ConversationPriority.HIGH,
            tags=(" VIP ", "vip", "Shipping"),
            idempotency_key="routing-key-1",
        )
    )

    conversation = result.workspace.conversation
    assert conversation.assigned_agent_id == "agent-2"
    assert conversation.queue_id == "general"
    assert conversation.priority is ConversationPriority.HIGH
    assert conversation.tags == ("shipping", "vip")


@pytest.mark.asyncio
async def test_agent_without_routing_scope_cannot_reassign_conversation() -> None:
    service = SupportOperationsService(InMemorySupportOperations())
    agent = principal(
        scopes=frozenset(
            {
                "support:inbox:read",
                "support:inbox:reply:self",
                "support:inbox:takeover",
                "support:inbox:resolve:self",
            }
        )
    )

    with pytest.raises(PermissionError, match="support:inbox:routing"):
        await service.update_routing(
            UpdateConversationRoutingCommand(
                principal=agent,
                conversation_id="conversation-1",
                assigned_agent_id="agent-2",
                queue_id="general",
                priority=ConversationPriority.NORMAL,
                tags=(),
                idempotency_key="routing-denied-1",
            )
        )


@pytest.mark.asyncio
async def test_legacy_write_scope_is_not_a_manager_override() -> None:
    service = SupportOperationsService(InMemorySupportOperations())

    with pytest.raises(PermissionError, match="support:inbox:takeover"):
        await service.take_over(
            ConversationActionCommand(
                principal(scopes=frozenset({"support:inbox:read", "support:inbox:write"})),
                "conversation-1",
                "legacy-write-key",
            )
        )


@pytest.mark.asyncio
async def test_manager_updates_queue_members_with_deduplicated_agent_ids() -> None:
    adapter = InMemorySupportOperations()
    service = SupportOperationsService(adapter)
    manager = principal(scopes=frozenset({"support:inbox:manage"}))

    members = await service.update_queue_members(
        UpdateSupportQueueMembersCommand(
            principal=manager,
            queue_id="general",
            agent_ids=("agent-1", "agent-1", " agent-2 "),
            idempotency_key="queue-members-key",
        )
    )

    assert adapter.queue_members == ("agent-1", "agent-2")
    assert [item.agent_id for item in members] == ["agent-1", "agent-2"]


@pytest.mark.asyncio
async def test_memory_rejects_sensitive_content() -> None:
    service = SupportOperationsService(InMemorySupportOperations())

    with pytest.raises(ValueError, match="sensitive"):
        await service.upsert_memory(
            UpsertCustomerMemoryCommand(
                principal=principal(),
                customer_id="customer-1",
                kind=MemoryKind.PREFERENCE,
                content="My password is secret",
                source_type="verified_customer",
                source_id="conversation-1",
                confidence=0.95,
                consent_status="granted",
                idempotency_key="memory-key-1",
            )
        )


@pytest.mark.asyncio
async def test_memory_identifier_is_deterministic_for_idempotency_key() -> None:
    adapter = InMemorySupportOperations()
    service = SupportOperationsService(adapter)
    command = UpsertCustomerMemoryCommand(
        principal=principal(),
        customer_id="customer-1",
        kind=MemoryKind.PREFERENCE,
        content="Prefers concise answers",
        source_type="verified_customer",
        source_id="conversation-1",
        confidence=0.95,
        consent_status="granted",
        idempotency_key="memory-key-2",
    )

    first = await service.upsert_memory(command)
    second = await service.upsert_memory(command)

    assert first.items[0].memory_id == second.items[0].memory_id
    assert len(adapter.memories) == 1
