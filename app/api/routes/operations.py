from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.dependencies import authenticate_admin_request, get_container
from app.api.schemas.operations import (
    AgentMessageRequest,
    CannedReplyResponse,
    ConversationMessage,
    ConversationRoutingRequest,
    ConversationWorkspaceResponse,
    CreateCannedReplyRequest,
    CreateSupportQueueRequest,
    CustomerDirectoryItemResponse,
    CustomerDirectoryResponse,
    CustomerMemoryItemResponse,
    CustomerMemoryListResponse,
    IdempotentActionRequest,
    InboxCountsResponse,
    InboxItem,
    InboxResponse,
    InternalNoteRequest,
    ManualHandoffRequest,
    MemoryCandidateListResponse,
    MemoryCandidateResponse,
    ProposeMemoryCandidateRequest,
    ResolutionEpisodeListResponse,
    ResolutionEpisodeResponse,
    ReviewMemoryCandidateRequest,
    SiteItem,
    SiteListResponse,
    SupportAgentOptionResponse,
    SupportConfigurationResponse,
    SupportQueueMemberResponse,
    SupportQueueMembersResponse,
    SupportQueueResponse,
    UpdateSupportQueueMembersRequest,
    UpdateSupportQueueRequest,
    UpsertCustomerMemoryRequest,
)
from app.application.dto import (
    AddInternalNoteCommand,
    ConversationActionCommand,
    CreateCannedReplyCommand,
    CreateManualHandoffCommand,
    CreateSupportQueueCommand,
    DeleteCustomerMemoryCommand,
    DeleteResolutionEpisodeCommand,
    GetConversationQuery,
    ListCustomerMemoryQuery,
    ListInboxQuery,
    ListMemoryCandidatesQuery,
    ListResolutionEpisodesQuery,
    ListSitesQuery,
    ListSupportConfigurationQuery,
    ProposeMemoryCandidateCommand,
    ReviewMemoryCandidateCommand,
    SendAgentMessageCommand,
    UpdateConversationRoutingCommand,
    UpdateSupportQueueCommand,
    UpdateSupportQueueMembersCommand,
    UpsertCustomerMemoryCommand,
)
from app.application.dto.operations import (
    ListCustomerConversationsQuery,
    ListCustomersQuery,
)
from app.bootstrap.container import Container
from app.domain.models import (
    ConversationPriority,
    ConversationStatus,
    MemoryKind,
    OwnershipMode,
)

router = APIRouter(prefix="/v1/admin", tags=["support-operations"])


@router.get("/sites", response_model=SiteListResponse)
async def list_sites(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> SiteListResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await container.support_operations_service.list_sites(ListSitesQuery(principal))
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return SiteListResponse(
        items=[
            SiteItem(
                site_id=item.site_id,
                name=item.name,
                base_url=item.base_url,
                status=item.status,
            )
            for item in result.items
        ]
    )


@router.get("/inbox", response_model=InboxResponse)
async def list_inbox(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
    conversation_status: Annotated[str | None, Query(alias="status")] = None,
    ownership: str | None = None,
    site_id: str | None = None,
    queue_id: str | None = None,
    priority: str | None = None,
    tag: str | None = None,
    search: Annotated[str | None, Query(max_length=200)] = None,
    sla_risk: bool = False,
    priority_risk: bool = False,
    high_intent: bool | None = None,
    unread_only: bool = False,
    mine_only: bool = False,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> InboxResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await container.support_operations_service.list_inbox(
            ListInboxQuery(
                principal=principal,
                status=(ConversationStatus(conversation_status) if conversation_status else None),
                ownership_mode=OwnershipMode(ownership) if ownership else None,
                site_id=site_id,
                queue_id=queue_id,
                priority=ConversationPriority(priority) if priority else None,
                tag=tag,
                search=search,
                sla_risk_only=sla_risk,
                priority_risk_only=priority_risk or bool(high_intent),
                unread_only=unread_only,
                mine_only=mine_only,
                limit=limit,
                cursor=cursor,
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return InboxResponse(
        items=[_inbox_item(item) for item in result.items],
        next_cursor=result.next_cursor,
        total=result.total,
    )


@router.get("/inbox/counts", response_model=InboxCountsResponse)
async def inbox_counts(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
    site_id: str | None = None,
) -> InboxCountsResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await container.support_operations_service.inbox_counts(
            ListInboxQuery(principal=principal, site_id=site_id)
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    counts = asdict(result.counts)
    return InboxCountsResponse(**counts, high_intent=result.counts.priority_risk)


@router.get("/customers", response_model=CustomerDirectoryResponse)
async def list_customers(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
    site_id: str | None = None,
    search: Annotated[str | None, Query(max_length=200)] = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> CustomerDirectoryResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await container.support_operations_service.list_customers(
            ListCustomersQuery(principal, site_id, search, limit, cursor)
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return CustomerDirectoryResponse(
        items=[
            CustomerDirectoryItemResponse(
                customer_id=item.customer_id,
                display_name=item.display_name,
                conversation_count=item.conversation_count,
                last_conversation_at=item.last_conversation_at,
            )
            for item in result.items
        ],
        next_cursor=result.next_cursor,
        total=result.total,
    )


@router.get("/customers/{customer_id}/conversations", response_model=InboxResponse)
async def list_customer_conversations(
    customer_id: str,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
    site_id: str | None = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> InboxResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await container.support_operations_service.list_customer_conversations(
            ListCustomerConversationsQuery(principal, customer_id, site_id, limit, cursor)
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return InboxResponse(
        items=[_inbox_item(item) for item in result.items],
        next_cursor=result.next_cursor,
        total=result.total,
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationWorkspaceResponse)
async def get_conversation(
    conversation_id: str,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> ConversationWorkspaceResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await container.support_operations_service.get_conversation(
            GetConversationQuery(principal, conversation_id)
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _workspace_response(result.workspace)


@router.post(
    "/conversations/{conversation_id}/notes",
    response_model=ConversationWorkspaceResponse,
)
async def add_internal_note(
    conversation_id: str,
    payload: InternalNoteRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> ConversationWorkspaceResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await container.support_operations_service.add_internal_note(
            AddInternalNoteCommand(
                principal=principal,
                conversation_id=conversation_id,
                content=payload.content,
                idempotency_key=payload.idempotency_key,
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _workspace_response(result.workspace)


@router.post(
    "/conversations/{conversation_id}/routing",
    response_model=ConversationWorkspaceResponse,
)
async def update_conversation_routing(
    conversation_id: str,
    payload: ConversationRoutingRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> ConversationWorkspaceResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await container.support_operations_service.update_routing(
            UpdateConversationRoutingCommand(
                principal=principal,
                conversation_id=conversation_id,
                assigned_agent_id=payload.assigned_agent_id,
                queue_id=payload.queue_id,
                priority=ConversationPriority(payload.priority),
                tags=tuple(payload.tags),
                idempotency_key=payload.idempotency_key,
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return _workspace_response(result.workspace)


@router.post(
    "/conversations/{conversation_id}/handoffs",
    response_model=ConversationWorkspaceResponse,
)
async def create_manual_handoff(
    conversation_id: str,
    payload: ManualHandoffRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> ConversationWorkspaceResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await container.support_operations_service.create_manual_handoff(
            CreateManualHandoffCommand(
                principal=principal,
                conversation_id=conversation_id,
                summary=payload.summary,
                queue_id=payload.queue_id,
                priority=ConversationPriority(payload.priority),
                idempotency_key=payload.idempotency_key,
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _workspace_response(result.workspace)


@router.get("/support-configuration", response_model=SupportConfigurationResponse)
async def get_support_configuration(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> SupportConfigurationResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await container.support_operations_service.list_configuration(
            ListSupportConfigurationQuery(principal)
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return SupportConfigurationResponse(
        queues=[
            SupportQueueResponse(
                queue_id=item.queue_id,
                name=item.name,
                description=item.description,
                is_default=item.is_default,
                status=item.status,
                site_id=item.site_id,
            )
            for item in result.queues
        ],
        agents=[
            SupportAgentOptionResponse(agent_id=item.agent_id, display_name=item.display_name)
            for item in result.agents
        ],
        canned_replies=[
            CannedReplyResponse(
                reply_id=item.reply_id,
                title=item.title,
                content=item.content,
                shortcut=item.shortcut,
            )
            for item in result.canned_replies
        ],
    )


@router.post("/queues", response_model=SupportQueueResponse)
async def create_support_queue(
    payload: CreateSupportQueueRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> SupportQueueResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        queue = await container.support_operations_service.create_queue(
            CreateSupportQueueCommand(
                principal=principal,
                name=payload.name,
                description=payload.description,
                is_default=payload.is_default,
                site_id=payload.site_id,
                idempotency_key=payload.idempotency_key,
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return SupportQueueResponse(**_queue_payload(queue))


@router.patch("/queues/{queue_id}", response_model=SupportQueueResponse)
async def update_support_queue(
    queue_id: str,
    payload: UpdateSupportQueueRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> SupportQueueResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        queue = await container.support_operations_service.update_queue(
            UpdateSupportQueueCommand(
                principal=principal,
                queue_id=queue_id,
                name=payload.name,
                description=payload.description,
                status=payload.status,
                is_default=payload.is_default,
                site_id=payload.site_id,
                idempotency_key=payload.idempotency_key,
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return SupportQueueResponse(**_queue_payload(queue))


@router.get("/queues/{queue_id}/members", response_model=SupportQueueMembersResponse)
async def list_support_queue_members(
    queue_id: str,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> SupportQueueMembersResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        members = await container.support_operations_service.list_queue_members(principal, queue_id)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return SupportQueueMembersResponse(
        items=[
            SupportQueueMemberResponse(
                agent_id=item.agent_id,
                display_name=item.display_name,
                role=item.role,
                status=item.status,
            )
            for item in members
        ]
    )


@router.put("/queues/{queue_id}/members", response_model=SupportQueueMembersResponse)
async def update_support_queue_members(
    queue_id: str,
    payload: UpdateSupportQueueMembersRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> SupportQueueMembersResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        members = await container.support_operations_service.update_queue_members(
            UpdateSupportQueueMembersCommand(
                principal=principal,
                queue_id=queue_id,
                agent_ids=tuple(payload.agent_ids),
                idempotency_key=payload.idempotency_key,
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return SupportQueueMembersResponse(
        items=[
            SupportQueueMemberResponse(
                agent_id=item.agent_id,
                display_name=item.display_name,
                role=item.role,
                status=item.status,
            )
            for item in members
        ]
    )


@router.post("/canned-replies", response_model=CannedReplyResponse)
async def create_canned_reply(
    payload: CreateCannedReplyRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> CannedReplyResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await container.support_operations_service.create_canned_reply(
            CreateCannedReplyCommand(
                principal=principal,
                title=payload.title,
                content=payload.content,
                shortcut=payload.shortcut,
                idempotency_key=payload.idempotency_key,
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    item = result.canned_replies[0]
    return CannedReplyResponse(
        reply_id=item.reply_id,
        title=item.title,
        content=item.content,
        shortcut=item.shortcut,
    )


@router.post(
    "/conversations/{conversation_id}/takeover",
    response_model=ConversationWorkspaceResponse,
)
async def take_over(
    conversation_id: str,
    payload: IdempotentActionRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> ConversationWorkspaceResponse:
    return await _conversation_action(
        "take_over", conversation_id, payload.idempotency_key, request, container
    )


@router.post(
    "/conversations/{conversation_id}/read",
    response_model=ConversationWorkspaceResponse,
)
async def mark_read(
    conversation_id: str,
    payload: IdempotentActionRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> ConversationWorkspaceResponse:
    return await _conversation_action(
        "mark_read", conversation_id, payload.idempotency_key, request, container
    )


@router.post(
    "/conversations/{conversation_id}/release-to-ai",
    response_model=ConversationWorkspaceResponse,
)
async def release_to_ai(
    conversation_id: str,
    payload: IdempotentActionRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> ConversationWorkspaceResponse:
    return await _conversation_action(
        "release_to_ai", conversation_id, payload.idempotency_key, request, container
    )


@router.post(
    "/conversations/{conversation_id}/resolve",
    response_model=ConversationWorkspaceResponse,
)
async def resolve(
    conversation_id: str,
    payload: IdempotentActionRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> ConversationWorkspaceResponse:
    return await _conversation_action(
        "resolve", conversation_id, payload.idempotency_key, request, container
    )


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=ConversationWorkspaceResponse,
)
async def send_agent_message(
    conversation_id: str,
    payload: AgentMessageRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> ConversationWorkspaceResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await container.support_operations_service.send_agent_message(
            SendAgentMessageCommand(
                principal=principal,
                conversation_id=conversation_id,
                content=payload.content,
                idempotency_key=payload.idempotency_key,
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _workspace_response(result.workspace)


@router.get("/customers/{customer_id}/memory", response_model=CustomerMemoryListResponse)
async def list_customer_memory(
    customer_id: str,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> CustomerMemoryListResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await container.support_operations_service.list_memory(
            ListCustomerMemoryQuery(principal, customer_id)
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return CustomerMemoryListResponse(items=[_memory_item(item) for item in result.items])


@router.put("/customers/{customer_id}/memory", response_model=CustomerMemoryListResponse)
async def upsert_customer_memory(
    customer_id: str,
    payload: UpsertCustomerMemoryRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> CustomerMemoryListResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await container.support_operations_service.upsert_memory(
            UpsertCustomerMemoryCommand(
                principal=principal,
                customer_id=customer_id,
                kind=MemoryKind(payload.kind),
                content=payload.content,
                source_type=payload.source_type,
                source_id=payload.source_id,
                confidence=payload.confidence,
                consent_status=payload.consent_status,
                idempotency_key=payload.idempotency_key,
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return CustomerMemoryListResponse(items=[_memory_item(item) for item in result.items])


@router.delete("/customers/{customer_id}/memory/{memory_id}", status_code=204)
async def delete_customer_memory(
    customer_id: str,
    memory_id: str,
    payload: IdempotentActionRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> None:
    principal = await authenticate_admin_request(request, container)
    try:
        await container.support_operations_service.delete_memory(
            DeleteCustomerMemoryCommand(
                principal=principal,
                customer_id=customer_id,
                memory_id=memory_id,
                idempotency_key=payload.idempotency_key,
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


@router.get(
    "/customers/{customer_id}/memory/candidates",
    response_model=MemoryCandidateListResponse,
)
async def list_memory_candidates(
    customer_id: str,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> MemoryCandidateListResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await container.support_operations_service.list_memory_candidates(
            ListMemoryCandidatesQuery(principal, customer_id)
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return MemoryCandidateListResponse(items=[_candidate_item(item) for item in result.candidates])


@router.post(
    "/customers/{customer_id}/memory/candidates",
    response_model=MemoryCandidateListResponse,
)
async def propose_memory_candidate(
    customer_id: str,
    payload: ProposeMemoryCandidateRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> MemoryCandidateListResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await container.support_operations_service.propose_memory_candidate(
            ProposeMemoryCandidateCommand(
                principal=principal,
                customer_id=customer_id,
                conversation_id=payload.conversation_id,
                kind=MemoryKind(payload.kind),
                normalized_value=dict(payload.normalized_value),
                display_text=payload.display_text,
                source_message_ids=tuple(payload.source_message_ids),
                source_trace_id=payload.source_trace_id,
                confidence=payload.confidence,
                sensitivity=payload.sensitivity,
                consent_required=payload.consent_required,
                idempotency_key=payload.idempotency_key,
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return MemoryCandidateListResponse(items=[_candidate_item(item) for item in result.candidates])


@router.post(
    "/customers/{customer_id}/memory/candidates/{candidate_id}/review",
    response_model=MemoryCandidateListResponse,
)
async def review_memory_candidate(
    customer_id: str,
    candidate_id: str,
    payload: ReviewMemoryCandidateRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> MemoryCandidateListResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await container.support_operations_service.review_memory_candidate(
            ReviewMemoryCandidateCommand(
                principal=principal,
                customer_id=customer_id,
                candidate_id=candidate_id,
                approve=payload.approve,
                rejection_reason=payload.rejection_reason,
                idempotency_key=payload.idempotency_key,
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return MemoryCandidateListResponse(
        items=[_candidate_item(item) for item in result.candidates],
        memory=None if result.memory is None else _memory_item(result.memory),
    )


@router.get(
    "/customers/{customer_id}/episodes",
    response_model=ResolutionEpisodeListResponse,
)
async def list_resolution_episodes(
    customer_id: str,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> ResolutionEpisodeListResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await container.support_operations_service.list_resolution_episodes(
            ListResolutionEpisodesQuery(principal, customer_id)
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return ResolutionEpisodeListResponse(items=[_episode_item(item) for item in result.items])


@router.delete("/customers/{customer_id}/episodes/{episode_id}", status_code=204)
async def delete_resolution_episode(
    customer_id: str,
    episode_id: str,
    payload: IdempotentActionRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> None:
    principal = await authenticate_admin_request(request, container)
    try:
        await container.support_operations_service.delete_resolution_episode(
            DeleteResolutionEpisodeCommand(
                principal=principal,
                customer_id=customer_id,
                episode_id=episode_id,
                idempotency_key=payload.idempotency_key,
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


async def _conversation_action(
    action: str,
    conversation_id: str,
    idempotency_key: str,
    request: Request,
    container: Container,
) -> ConversationWorkspaceResponse:
    principal = await authenticate_admin_request(request, container)
    command = ConversationActionCommand(principal, conversation_id, idempotency_key)
    try:
        service_method = getattr(container.support_operations_service, action)
        result = await service_method(command)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _workspace_response(result.workspace)


def _inbox_item(item) -> InboxItem:  # type: ignore[no-untyped-def]
    return InboxItem(
        conversation_id=item.conversation_id,
        site_id=item.site_id,
        customer_id=item.customer_id,
        customer_display_name=item.customer_display_name,
        visitor_ip_address=item.visitor_ip_address,
        visitor_country_code=item.visitor_country_code,
        channel=item.channel,
        status=item.status.value,
        ownership_mode=item.ownership_mode.value,
        assigned_agent_id=item.assigned_agent_id,
        queue_id=item.queue_id,
        priority=item.priority.value,
        tags=list(item.tags),
        risk_level=item.risk_level,
        unread_count=item.unread_count,
        identity_verified=item.identity_verified,
        last_message_preview=item.last_message_preview,
        last_message_at=item.last_message_at,
        first_response_at=item.first_response_at,
        first_human_response_at=item.first_human_response_at,
        resolved_at=item.resolved_at,
        last_read_at=item.last_read_at,
        updated_at=item.updated_at,
        handoff_reason=item.handoff_reason,
        sla_due_at=item.sla_due_at,
        routing_version=item.routing_version,
    )


def _queue_payload(item) -> dict[str, object]:  # type: ignore[no-untyped-def]
    return {
        "queue_id": item.queue_id,
        "name": item.name,
        "description": item.description,
        "is_default": item.is_default,
        "status": item.status,
        "site_id": item.site_id,
    }


def _workspace_response(workspace) -> ConversationWorkspaceResponse:  # type: ignore[no-untyped-def]
    return ConversationWorkspaceResponse(
        conversation=_inbox_item(workspace.conversation),
        messages=[
            ConversationMessage(
                message_id=item.message_id,
                role=item.role,
                content=item.content,
                message_type=item.message_type,
                author_subject_id=item.author_subject_id,
                metadata=item.metadata,
                created_at=item.created_at,
            )
            for item in workspace.messages
        ],
        handoff_context=workspace.handoff_context,
    )


def _memory_item(item) -> CustomerMemoryItemResponse:  # type: ignore[no-untyped-def]
    return CustomerMemoryItemResponse(
        memory_id=item.memory_id,
        customer_id=item.customer_id,
        kind=item.kind.value,
        content=item.content,
        source_type=item.source_type,
        source_id=item.source_id,
        confidence=item.confidence,
        consent_status=item.consent_status,
        expires_at=item.expires_at,
        created_at=item.created_at,
        updated_at=item.updated_at,
        status=item.status,
        normalized_value=item.normalized_value,
        sensitivity=item.sensitivity,
        last_verified_at=item.last_verified_at,
        last_used_at=item.last_used_at,
        use_count=item.use_count,
        superseded_by=item.superseded_by,
        legacy=item.legacy,
    )


def _candidate_item(item) -> MemoryCandidateResponse:  # type: ignore[no-untyped-def]
    return MemoryCandidateResponse(
        candidate_id=item.candidate_id,
        customer_id=item.customer_id,
        conversation_id=item.conversation_id,
        kind=item.kind.value,
        normalized_value=item.normalized_value,
        display_text=item.display_text,
        source_message_ids=list(item.source_message_ids),
        source_trace_id=item.source_trace_id,
        confidence=item.confidence,
        sensitivity=item.sensitivity,
        consent_required=item.consent_required,
        status=item.status.value,
        rejection_reason=item.rejection_reason,
        reviewed_by=item.reviewed_by,
        reviewed_at=item.reviewed_at,
        created_at=item.created_at,
        expires_at=item.expires_at,
    )


def _episode_item(item) -> ResolutionEpisodeResponse:  # type: ignore[no-untyped-def]
    return ResolutionEpisodeResponse(
        episode_id=item.episode_id,
        customer_id=item.customer_id,
        conversation_id=item.conversation_id,
        intent=item.intent,
        issue_summary=item.issue_summary,
        product_reference=item.product_reference,
        order_reference=item.order_reference,
        actions_taken=list(item.actions_taken),
        resolution_summary=item.resolution_summary,
        resolution_source=item.resolution_source,
        resolved_at=item.resolved_at,
        reopened_at=item.reopened_at,
        confidence=item.confidence,
        expires_at=item.expires_at,
        status=item.status,
    )
