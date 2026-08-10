import asyncio
import json
from dataclasses import asdict
from hashlib import sha256
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ValidationError
from starlette.responses import JSONResponse, Response, StreamingResponse

from app.api.dependencies import get_container
from app.api.request_context import country_code, source_address, visitor_user_agent
from app.api.routes.widget_assets import widget_asset_version
from app.api.schemas.customer_experience import (
    PublicOfflineMessageRequest,
    PublicSatisfactionRequest,
)
from app.api.schemas.public_widget import (
    PublicWidgetBootstrapRequest,
    PublicWidgetChatRequest,
    PublicWidgetConversationStateRequest,
    PublicWidgetEventStreamRequest,
    PublicWidgetMessagesRequest,
    PublicWidgetPresenceRequest,
)
from app.application.dto import (
    ApplyAutomationCommand,
    AuthenticatePublicPresenceCommand,
    AuthenticatePublicWidgetCommand,
    BootstrapPublicWidgetCommand,
    CreateOfflineTicketCommand,
    GetPublicConversationStateQuery,
    HandleChatCommand,
    ListHumanMessagesQuery,
    RecordVisitorPresenceCommand,
    SubmitSatisfactionCommand,
)
from app.application.dto.widget import GetPublicWidgetAppearanceQuery
from app.application.services import (
    PublicWidgetAccessDeniedError,
    PublicWidgetQuotaExceededError,
    PublicWidgetRateLimitError,
)
from app.application.tenant_context import bind_tenant
from app.bootstrap.container import Container

router = APIRouter(prefix="/v1/public-widget", tags=["public-widget"])


@router.get("/appearance")
async def public_widget_appearance(
    public_widget_id: str,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> Response:
    origin = request.headers.get("origin", "")
    try:
        result = await _service(container).appearance(
            GetPublicWidgetAppearanceQuery(
                public_widget_id=public_widget_id,
                origin=origin,
                source_address=_source_address(request),
                correlation_id=request.state.correlation_id,
            )
        )
    except PublicWidgetRateLimitError:
        return _response(429, {"error": "rate_limited"}, origin)
    except PublicWidgetAccessDeniedError:
        return _response(403, {"error": "widget_access_denied"}, origin)
    version = result.widget_config_version
    asset_version = widget_asset_version()
    etag_source = f"appearance-v3:{version}:{asset_version}:{int(result.is_online)}"
    etag = f'"{sha256(etag_source.encode("utf-8")).hexdigest()[:32]}"'
    headers = _appearance_headers(origin, etag)
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    config = _public_widget_config(
        result.widget_config,
        container.settings.public_widget_base_url,
    )
    return JSONResponse(
        content={
            "schema_version": 3,
            "version": version,
            "config_version": version,
            "asset_version": asset_version,
            "agent_name": config["agent_name"],
            "online_message": config["online_message"],
            "welcome_message": config["welcome_message"],
            "offline_message": config["offline_message"],
            "agent_avatar_url": config.get("agent_avatar_url"),
            "launcher_image_url": config.get("launcher_image_url"),
            "launcher_image_fit": config["launcher_image_fit"],
            "primary_color": config["primary_color"],
            "position": config["position"],
            "mobile_enabled": config["mobile_enabled"],
            "offline_form_enabled": config["offline_form_enabled"],
            "csat_enabled": config["csat_enabled"],
            "default_language": config["default_language"],
            "is_online": result.is_online,
        },
        headers=headers,
    )


@router.post("/bootstrap")
async def bootstrap_public_widget(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> JSONResponse:
    origin = request.headers.get("origin", "")
    try:
        payload = await _payload(request, PublicWidgetBootstrapRequest)
        service = _service(container)
        result = await service.bootstrap(
            BootstrapPublicWidgetCommand(
                public_widget_id=payload.public_widget_id,
                origin=origin,
                source_address=_source_address(request),
                correlation_id=request.state.correlation_id,
                resume_token=payload.resume_token,
                session_token=payload.session_token,
            )
        )
    except PublicWidgetRateLimitError:
        return _response(429, {"error": "rate_limited"}, origin)
    except PublicWidgetAccessDeniedError:
        return _response(403, {"error": "widget_access_denied"}, origin)
    except (ValidationError, ValueError):
        return _response(422, {"error": "invalid_widget_request"}, origin)
    return _response(
        200,
        {
            "session_token": result.session_token,
            "expires_at": result.expires_at.isoformat(),
            "primary_language": result.primary_language,
            "widget_config": _public_widget_config(
                result.widget_config,
                container.settings.public_widget_base_url,
            ),
            "is_online": result.is_online,
            "resume_token": result.resume_token,
            "resume_expires_at": (
                result.resume_expires_at.isoformat() if result.resume_expires_at else None
            ),
            "widget_config_version": result.widget_config_version,
        },
        origin,
    )


@router.post("/session/refresh")
async def refresh_public_widget_session(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> JSONResponse:
    return await bootstrap_public_widget(request, container)


@router.post("/chat")
async def public_widget_chat(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> JSONResponse:
    origin = request.headers.get("origin", "")
    try:
        payload = await _payload(request, PublicWidgetChatRequest)
        service = _service(container)
        authenticated = await service.authenticate(
            AuthenticatePublicWidgetCommand(
                session_token=payload.session_token,
                origin=origin,
                source_address=_source_address(request),
                correlation_id=request.state.correlation_id,
                operation="chat",
                request_id=payload.request_id,
            )
        )
        bind_tenant(authenticated.principal.tenant_id)
        lease = await service.acquire_chat_capacity(authenticated.principal)
        if lease is None:
            return _response(
                503,
                {"error": "chat_capacity_exhausted"},
                origin,
                extra_headers={"Retry-After": "2"},
            )
        try:
            await service.admit_chat(
                principal=authenticated.principal,
                request_id=payload.request_id,
            )
            async with asyncio.timeout(container.settings.widget_chat_deadline_seconds):
                result = await container.chat_service.execute(
                    HandleChatCommand(
                        conversation_id=payload.conversation_id,
                        message=payload.message,
                        principal=authenticated.principal,
                        page_path=payload.page_path,
                        request_received_at=getattr(request.state, "request_received_at", None),
                        request_route="/v1/public-widget/chat",
                        visitor_ip_address=source_address(request),
                        visitor_country_code=country_code(request),
                    )
                )
        finally:
            await service.release_chat_capacity(lease)
        response = result.response
        experience_service = getattr(container, "customer_experience_service", None)
        if experience_service is None:
            executions = ()
        else:
            try:
                executions = await experience_service.apply_automation(
                    ApplyAutomationCommand(
                        principal=authenticated.principal,
                        conversation_id=response.conversation_id,
                        page_path=payload.page_path,
                        user_intent=(
                            response.memory_state.last_intent if response.memory_state else None
                        ),
                        risk_level=int(response.risk_level),
                        dwell_seconds=payload.dwell_seconds,
                        request_id=payload.request_id,
                    )
                )
            except (LookupError, RuntimeError, ValueError):
                executions = ()
    except PublicWidgetRateLimitError:
        return _response(429, {"error": "rate_limited"}, origin)
    except PublicWidgetQuotaExceededError:
        return _response(429, {"error": "daily_quota_exceeded"}, origin)
    except TimeoutError:
        return _response(
            503,
            {"error": "chat_deadline_exceeded"},
            origin,
            extra_headers={"Retry-After": "2"},
        )
    except PublicWidgetAccessDeniedError:
        return _response(401, {"error": "widget_session_invalid"}, origin)
    except (ValidationError, ValueError):
        return _response(422, {"error": "invalid_widget_request"}, origin)
    return _response(
        200,
        {
            "conversation_id": response.conversation_id,
            "message": response.message,
            "kind": response.kind.value,
            "risk_level": int(response.risk_level),
            "trace_id": response.trace_id,
            "handoff_id": response.handoff_id,
            "citations": list(response.citations),
            "related_links": list(response.related_links),
            "recommended_products": [
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
                for item in response.recommended_products
            ],
            "automation_actions": [
                action for execution in executions for action in execution.actions_applied
            ],
        },
        origin,
    )


@router.post("/presence")
async def public_widget_presence(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> JSONResponse:
    origin = request.headers.get("origin", "")
    try:
        payload = await _payload(request, PublicWidgetPresenceRequest)
        service = _service(container)
        if payload.session_token:
            authenticated = await service.authenticate(
                AuthenticatePublicWidgetCommand(
                    session_token=payload.session_token,
                    origin=origin,
                    source_address=_source_address(request),
                    correlation_id=request.state.correlation_id,
                    operation="presence",
                )
            )
            presence_token = None
            presence_token_expires_at = None
        else:
            authenticated = await service.authenticate_presence(
                AuthenticatePublicPresenceCommand(
                    visitor_id=payload.visitor_id,
                    origin=origin,
                    source_address=_source_address(request),
                    correlation_id=request.state.correlation_id,
                    public_widget_id=payload.public_widget_id,
                    presence_token=payload.presence_token,
                )
            )
            presence_token = authenticated.presence_token
            presence_token_expires_at = authenticated.expires_at
        if payload.conversation_id and not authenticated.principal.visitor_session_id:
            raise ValueError("conversation binding requires a session-bound credential")
        bind_tenant(authenticated.principal.tenant_id)
        presence = await container.visitor_presence_service.record(
            RecordVisitorPresenceCommand(
                principal=authenticated.principal,
                visitor_id=payload.visitor_id,
                conversation_id=payload.conversation_id,
                page_path=payload.page_path,
                # Browser input is advisory. Only the server-side site-key
                # connector endpoint may supply trusted page taxonomy.
                page_kind=None,
                page_title=payload.page_title,
                referrer=payload.referrer,
                source_address=source_address(request),
                country_code=country_code(request),
                user_agent=visitor_user_agent(request),
                language=payload.language,
                timezone=payload.timezone,
                page_view_id=payload.page_view_id,
                widget_state=payload.widget_state,
                presence_source=payload.presence_source,
                runtime_version=payload.runtime_version,
                config_version=payload.config_version,
                connector_type=payload.connector_type,
                connector_version=payload.connector_version,
            )
        )
    except PublicWidgetRateLimitError:
        return _response(429, {"error": "rate_limited"}, origin)
    except PublicWidgetAccessDeniedError:
        return _response(401, {"error": "widget_session_invalid"}, origin)
    except (PermissionError, ValidationError, ValueError):
        return _response(422, {"error": "invalid_widget_request"}, origin)
    return _response(
        200,
        {
            "status": "ok",
            "last_seen_at": presence.last_seen_at.isoformat(),
            "presence_token": presence_token,
            "presence_token_expires_at": (
                presence_token_expires_at.isoformat() if presence_token_expires_at else None
            ),
        },
        origin,
    )


@router.post("/messages")
async def public_widget_messages(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> JSONResponse:
    origin = request.headers.get("origin", "")
    try:
        payload = await _payload(request, PublicWidgetMessagesRequest)
        authenticated = await _service(container).authenticate(
            AuthenticatePublicWidgetCommand(
                session_token=payload.session_token,
                origin=origin,
                source_address=_source_address(request),
                correlation_id=request.state.correlation_id,
                operation="messages",
            )
        )
        bind_tenant(authenticated.principal.tenant_id)
        result = await container.chat_service.list_human_messages(
            ListHumanMessagesQuery(
                principal=authenticated.principal,
                conversation_id=payload.conversation_id,
                limit=payload.limit,
                after_cursor=payload.after_cursor,
            )
        )
    except PublicWidgetRateLimitError:
        return _response(429, {"error": "rate_limited"}, origin)
    except PublicWidgetAccessDeniedError:
        return _response(401, {"error": "widget_session_invalid"}, origin)
    except (PermissionError, ValidationError, ValueError):
        return _response(422, {"error": "invalid_widget_request"}, origin)
    return _response(
        200,
        {
            "items": [
                {
                    "message_id": item.message_id,
                    "content": item.content,
                    "created_at": item.created_at.isoformat(),
                }
                for item in result.items
            ],
            "conversation_status": result.conversation_status,
            "handoff_active": result.handoff_active,
            "next_cursor": result.next_cursor,
        },
        origin,
    )


@router.post("/conversation-state")
async def public_widget_conversation_state(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> JSONResponse:
    origin = request.headers.get("origin", "")
    try:
        payload = await _payload(request, PublicWidgetConversationStateRequest)
        authenticated = await _service(container).authenticate(
            AuthenticatePublicWidgetCommand(
                session_token=payload.session_token,
                origin=origin,
                source_address=_source_address(request),
                correlation_id=request.state.correlation_id,
                operation="conversation-state",
            )
        )
        bind_tenant(authenticated.principal.tenant_id)
        result = await container.chat_service.get_public_conversation_state(
            GetPublicConversationStateQuery(
                principal=authenticated.principal,
                conversation_id=payload.conversation_id,
            )
        )
    except PublicWidgetRateLimitError:
        return _response(429, {"error": "rate_limited"}, origin)
    except PublicWidgetAccessDeniedError:
        return _response(401, {"error": "widget_session_invalid"}, origin)
    except (PermissionError, ValidationError, ValueError):
        return _response(422, {"error": "invalid_widget_request"}, origin)
    return _response(
        200,
        {
            "exists": result.exists,
            "conversation_status": result.conversation_status,
            "handoff_active": result.handoff_active,
            "handoff_id": result.handoff_id,
            "last_human_cursor": result.last_human_cursor,
        },
        origin,
    )


@router.post("/event-stream", response_model=None)
async def public_widget_event_stream(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> Response:
    origin = request.headers.get("origin", "")
    event_hub = getattr(container, "public_widget_event_hub", None)
    if event_hub is None:
        return _response(
            503,
            {"error": "event_stream_unavailable"},
            origin,
            extra_headers={"Retry-After": "15"},
        )
    try:
        payload = await _payload(request, PublicWidgetEventStreamRequest)
        authenticated = await _service(container).authenticate(
            AuthenticatePublicWidgetCommand(
                session_token=payload.session_token,
                origin=origin,
                source_address=_source_address(request),
                correlation_id=request.state.correlation_id,
                operation="events",
            )
        )
        principal = authenticated.principal
        bind_tenant(principal.tenant_id)
        state = await container.chat_service.get_public_conversation_state(
            GetPublicConversationStateQuery(principal, payload.conversation_id)
        )
        if not state.exists or not state.handoff_active or not principal.site_id:
            return _response(409, {"error": "handoff_not_active"}, origin)
    except PublicWidgetRateLimitError:
        return _response(429, {"error": "rate_limited"}, origin)
    except PublicWidgetAccessDeniedError:
        return _response(401, {"error": "widget_session_invalid"}, origin)
    except (PermissionError, ValidationError, ValueError):
        return _response(422, {"error": "invalid_widget_request"}, origin)

    async def stream():  # type: ignore[no-untyped-def]
        loop = asyncio.get_running_loop()
        deadline = loop.time() + container.settings.widget_sse_max_connection_seconds
        container.request_metrics.adjust_widget_sse_connections(1)
        try:
            async with event_hub.subscribe(
                tenant_id=principal.tenant_id,
                site_id=principal.site_id or "",
                conversation_id=payload.conversation_id,
            ) as queue:
                yield "retry: 5000\nevent: connected\ndata: {}\n\n"
                while loop.time() < deadline:
                    try:
                        event = await asyncio.wait_for(
                            queue.get(),
                            timeout=container.settings.widget_sse_heartbeat_seconds,
                        )
                    except TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    data = json.dumps(
                        {
                            "event_type": event.event_type,
                            "conversation_id": event.resource_id,
                        },
                        separators=(",", ":"),
                    )
                    yield f"id: {event.event_id}\nevent: message-available\ndata: {data}\n\n"
                yield "event: reconnect\ndata: {}\n\n"
        finally:
            container.request_metrics.adjust_widget_sse_connections(-1)

    headers = {
        "Access-Control-Allow-Origin": origin.rstrip("/"),
        "Cache-Control": "no-cache, no-store",
        "X-Accel-Buffering": "no",
        "X-Content-Type-Options": "nosniff",
    }
    return StreamingResponse(stream(), media_type="text/event-stream", headers=headers)


@router.post("/satisfaction")
async def public_widget_satisfaction(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> JSONResponse:
    origin = request.headers.get("origin", "")
    try:
        payload = await _payload(request, PublicSatisfactionRequest)
        authenticated = await _service(container).authenticate(
            AuthenticatePublicWidgetCommand(
                session_token=payload.session_token,
                origin=origin,
                source_address=_source_address(request),
                correlation_id=request.state.correlation_id,
                operation="satisfaction",
            )
        )
        bind_tenant(authenticated.principal.tenant_id)
        await container.customer_experience_service.submit_satisfaction(
            SubmitSatisfactionCommand(
                authenticated.principal,
                payload.conversation_id,
                payload.score,
                payload.comment,
                payload.request_id,
            )
        )
    except PublicWidgetRateLimitError:
        return _response(429, {"error": "rate_limited"}, origin)
    except PublicWidgetAccessDeniedError:
        return _response(401, {"error": "widget_session_invalid"}, origin)
    except (PermissionError, ValidationError, ValueError):
        return _response(422, {"error": "invalid_satisfaction_request"}, origin)
    return _response(200, {"status": "recorded"}, origin)


@router.post("/offline-message")
async def public_widget_offline_message(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> JSONResponse:
    origin = request.headers.get("origin", "")
    try:
        payload = await _payload(request, PublicOfflineMessageRequest)
        authenticated = await _service(container).authenticate(
            AuthenticatePublicWidgetCommand(
                session_token=payload.session_token,
                origin=origin,
                source_address=_source_address(request),
                correlation_id=request.state.correlation_id,
                operation="offline-message",
            )
        )
        bind_tenant(authenticated.principal.tenant_id)
        ticket_id = await container.customer_experience_service.create_offline_ticket(
            CreateOfflineTicketCommand(
                authenticated.principal,
                payload.conversation_id,
                payload.email,
                payload.message,
                payload.page_path,
                payload.request_id,
            )
        )
    except PublicWidgetRateLimitError:
        return _response(429, {"error": "rate_limited"}, origin)
    except PublicWidgetAccessDeniedError:
        return _response(401, {"error": "widget_session_invalid"}, origin)
    except (PermissionError, ValidationError, ValueError):
        return _response(422, {"error": "invalid_offline_message"}, origin)
    return _response(201, {"status": "created", "ticket_id": ticket_id}, origin)


async def _payload[Payload: BaseModel](request: Request, model: type[Payload]) -> Payload:
    body = await request.body()
    if not body or len(body) > 16_384:
        raise ValueError("invalid public widget payload")
    return model.model_validate_json(body)


def _service(container: Container):  # type: ignore[no-untyped-def]
    if container.public_widget_session_service is None:
        raise PublicWidgetAccessDeniedError("public widget service is unavailable")
    return container.public_widget_session_service


def _source_address(request: Request) -> str:
    return source_address(request)


def _response(
    status_code: int,
    payload: dict[str, object],
    origin: str,
    *,
    extra_headers: dict[str, str] | None = None,
) -> JSONResponse:
    headers = {
        "Cache-Control": "no-store",
        "Vary": "Origin",
        "X-Content-Type-Options": "nosniff",
    }
    if origin.startswith(("https://", "http://")):
        headers["Access-Control-Allow-Origin"] = origin.rstrip("/")
    if extra_headers:
        headers.update(extra_headers)
    return JSONResponse(status_code=status_code, content=payload, headers=headers)


def _public_widget_config(
    config,
    public_widget_base_url: str,  # type: ignore[no-untyped-def]
) -> dict[str, object]:
    payload = asdict(config)
    launcher_asset_id = payload.pop("launcher_asset_id", None)
    avatar_asset_id = payload.pop("agent_avatar_asset_id", None)
    media_base_url = public_widget_base_url.rstrip("/")
    payload["launcher_image_url"] = (
        f"{media_base_url}/v1/widget-media/{launcher_asset_id}?size=128"
        if launcher_asset_id
        else None
    )
    payload["agent_avatar_url"] = (
        f"{media_base_url}/v1/widget-media/{avatar_asset_id}?size=64" if avatar_asset_id else None
    )
    return payload


def _appearance_headers(origin: str, etag: str) -> dict[str, str]:
    headers = {
        "Cache-Control": "public, max-age=0, s-maxage=60, stale-while-revalidate=300",
        "ETag": etag,
        "Access-Control-Expose-Headers": "ETag",
        "Vary": "Origin",
        "X-Content-Type-Options": "nosniff",
    }
    if origin.startswith(("https://", "http://")):
        headers["Access-Control-Allow-Origin"] = origin.rstrip("/")
    return headers
