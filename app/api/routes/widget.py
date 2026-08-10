from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from starlette.responses import JSONResponse

from app.api.dependencies import get_container
from app.api.request_context import country_code, source_address
from app.api.routes.widget_assets import widget_asset_version
from app.api.schemas import ChatRequest, ChatResponse, WidgetMessagesRequest
from app.application.dto import HandleChatCommand, ListHumanMessagesQuery
from app.application.tenant_context import bind_tenant
from app.bootstrap.container import Container

router = APIRouter(prefix="/v1/widget", tags=["widget"])


@router.get("/manifest")
async def widget_connector_manifest(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
    site_key: Annotated[str, Header(alias="X-Agent-Site-Key", min_length=32, max_length=256)],
) -> JSONResponse:
    try:
        principal = await container.widget_authentication.authenticate_site(
            site_key=site_key,
            correlation_id=request.state.correlation_id,
        )
        bind_tenant(principal.tenant_id)
        site = await container.site_administration_service.connector_manifest(principal)
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid widget site credential",
        ) from exc
    asset_version = widget_asset_version()
    base_url = container.settings.public_widget_base_url.rstrip("/")
    return JSONResponse(
        content={
            "public_widget_id": site.public_widget_id,
            "script_url": f"{base_url}/widget.js?v={asset_version}",
            "asset_version": asset_version,
            "config_version": principal.site_identity_version,
            "connector_mode": "public",
        },
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/chat", response_model=ChatResponse)
async def widget_chat(
    payload: ChatRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
    site_key: Annotated[str, Header(alias="X-Agent-Site-Key", min_length=8, max_length=256)],
) -> ChatResponse:
    try:
        principal = await container.widget_authentication.authenticate_site(
            site_key=site_key,
            correlation_id=request.state.correlation_id,
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid widget site credential",
        ) from exc
    bind_tenant(principal.tenant_id)

    result = await container.chat_service.execute(
        HandleChatCommand(
            conversation_id=payload.conversation_id,
            message=payload.message,
            principal=principal,
            page_path=payload.page_path,
            request_received_at=getattr(request.state, "request_received_at", None),
            request_route="/v1/widget/chat",
            visitor_ip_address=source_address(
                request,
                trusted_forwarded_address=request.headers.get("x-agent-visitor-ip"),
            ),
            visitor_country_code=country_code(
                request,
                trusted_forwarded_country=request.headers.get("x-agent-visitor-country"),
            ),
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


@router.post("/messages")
async def widget_messages(
    payload: WidgetMessagesRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
    site_key: Annotated[str, Header(alias="X-Agent-Site-Key", min_length=8, max_length=256)],
) -> dict[str, list[dict[str, str]]]:
    try:
        principal = await container.widget_authentication.authenticate_site(
            site_key=site_key,
            correlation_id=request.state.correlation_id,
        )
        bind_tenant(principal.tenant_id)
        result = await container.chat_service.list_human_messages(
            ListHumanMessagesQuery(
                principal=principal,
                conversation_id=payload.conversation_id,
            )
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid widget site credential",
        ) from exc
    return {
        "items": [
            {
                "message_id": item.message_id,
                "content": item.content,
                "created_at": item.created_at.isoformat(),
            }
            for item in result.items
        ]
    }
