from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import authenticate_admin_request, get_container
from app.api.schemas import ChatRequest, ChatResponse
from app.application.dto import HandleChatCommand
from app.bootstrap.container import Container

router = APIRouter(prefix="/v1", tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> ChatResponse:
    principal = await authenticate_admin_request(request, container)
    result = await container.chat_service.execute(
        HandleChatCommand(
            conversation_id=payload.conversation_id,
            message=payload.message,
            principal=principal,
            page_path=payload.page_path,
            request_received_at=getattr(request.state, "request_received_at", None),
            request_route="/v1/chat",
        )
    )
    response = result.response
    return ChatResponse(
        conversation_id=response.conversation_id,
        message=response.message,
        kind=response.kind.value,
        risk_level=int(response.risk_level),
        trace_id=response.trace_id,
        handoff_id=response.handoff_id,
        citations=list(response.citations),
        related_links=list(response.related_links),
        recommended_products=list(response.recommended_products),
    )
