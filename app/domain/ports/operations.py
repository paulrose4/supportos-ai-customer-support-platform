from typing import Protocol

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
    MemoryKind,
    OwnershipMode,
    ResolutionEpisode,
    SupportAgentOption,
    SupportQueue,
    SupportQueueMember,
    SupportSite,
)


class SupportOperationsPort(Protocol):
    async def list_sites(self, *, tenant_id: str) -> list[SupportSite]: ...

    async def inbox_counts(
        self, *, tenant_id: str, site_id: str | None, assigned_agent_id: str
    ) -> InboxCounts: ...

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
    ) -> tuple[list[InboxConversation], str | None, int]: ...

    async def list_customers(
        self,
        *,
        tenant_id: str,
        site_id: str | None,
        search: str | None,
        limit: int,
        cursor: str | None = None,
    ) -> tuple[list[CustomerDirectoryItem], str | None, int]: ...

    async def get_workspace(
        self, *, tenant_id: str, conversation_id: str
    ) -> ConversationWorkspace | None: ...

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
    ) -> ConversationWorkspace: ...

    async def append_agent_message(
        self,
        *,
        tenant_id: str,
        conversation_id: str,
        actor_subject_id: str,
        correlation_id: str,
        content: str,
        idempotency_key: str,
    ) -> ConversationWorkspace: ...

    async def append_internal_note(
        self,
        *,
        tenant_id: str,
        conversation_id: str,
        actor_subject_id: str,
        correlation_id: str,
        content: str,
        idempotency_key: str,
    ) -> ConversationWorkspace: ...

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
    ) -> ConversationWorkspace: ...

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
    ) -> ConversationWorkspace: ...

    async def list_queues(self, *, tenant_id: str) -> list[SupportQueue]: ...

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
    ) -> SupportQueue: ...

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
    ) -> SupportQueue: ...

    async def list_queue_members(
        self, *, tenant_id: str, queue_id: str
    ) -> list[SupportQueueMember]: ...

    async def update_queue_members(
        self,
        *,
        tenant_id: str,
        queue_id: str,
        agent_ids: tuple[str, ...],
        actor_subject_id: str,
        correlation_id: str,
        idempotency_key: str,
    ) -> list[SupportQueueMember]: ...

    async def list_agent_options(self, *, tenant_id: str) -> list[SupportAgentOption]: ...

    async def list_canned_replies(self, *, tenant_id: str) -> list[CannedReply]: ...

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
    ) -> CannedReply: ...

    async def list_customer_memory(
        self, *, tenant_id: str, customer_id: str
    ) -> list[CustomerMemoryItem]: ...

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
    ) -> CustomerMemoryItem: ...

    async def delete_customer_memory(
        self,
        *,
        tenant_id: str,
        customer_id: str,
        memory_id: str,
        actor_subject_id: str,
        correlation_id: str,
        idempotency_key: str,
    ) -> bool: ...

    async def list_memory_candidates(
        self, *, tenant_id: str, customer_id: str
    ) -> list[MemoryCandidate]: ...

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
    ) -> MemoryCandidate: ...

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
    ) -> tuple[MemoryCandidate, CustomerMemoryItem | None]: ...

    async def list_resolution_episodes(
        self, *, tenant_id: str, customer_id: str
    ) -> list[ResolutionEpisode]: ...

    async def delete_resolution_episode(
        self,
        *,
        tenant_id: str,
        customer_id: str,
        episode_id: str,
        actor_subject_id: str,
        correlation_id: str,
        idempotency_key: str,
    ) -> bool: ...
