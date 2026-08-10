import asyncio
from contextlib import suppress
from uuid import uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from app.api.middleware import websocket_origin_is_trusted
from app.application.dto import AuthenticateAdminCommand

router = APIRouter(tags=["realtime"])


@router.websocket("/v1/ws/support")
async def support_events(websocket: WebSocket) -> None:
    container = websocket.app.state.container
    correlation_id = websocket.headers.get("x-correlation-id") or str(uuid4())
    try:
        if container.admin_session_service is not None:
            if container.settings.enforce_browser_origin and not websocket_origin_is_trusted(
                websocket.headers.get("origin"), container.settings.allowed_origins
            ):
                raise PermissionError("trusted WebSocket origin is required")
            token = websocket.cookies.get(container.settings.admin_session_cookie_name, "")
            result = await container.admin_session_service.authenticate(
                AuthenticateAdminCommand(token, correlation_id)
            )
            principal = result.principal
        else:
            principal = await container.authentication.authenticate(correlation_id)
    except PermissionError:
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION, reason="authentication required"
        )
        return

    await websocket.accept()
    await websocket.send_json(
        {
            "event_type": "realtime.connected",
            "tenant_id": principal.tenant_id,
            "resource_type": "connection",
            "resource_id": correlation_id,
            "payload": {"subject_id": principal.subject_id},
        }
    )
    async with container.realtime_hub.subscribe(principal.tenant_id) as queue:
        event_task = asyncio.create_task(queue.get())
        receive_task = asyncio.create_task(websocket.receive_text())
        try:
            while True:
                done, _ = await asyncio.wait(
                    {event_task, receive_task}, return_when=asyncio.FIRST_COMPLETED
                )
                if receive_task in done:
                    message = receive_task.result()
                    if message == "ping":
                        await websocket.send_text("pong")
                    receive_task = asyncio.create_task(websocket.receive_text())
                if event_task in done:
                    event = event_task.result()
                    await websocket.send_json(
                        {
                            "event_id": event.event_id,
                            "tenant_id": event.tenant_id,
                            "event_type": event.event_type,
                            "resource_type": event.resource_type,
                            "resource_id": event.resource_id,
                            "payload": event.payload,
                            "occurred_at": (
                                event.occurred_at.isoformat() if event.occurred_at else None
                            ),
                        }
                    )
                    event_task = asyncio.create_task(queue.get())
        except WebSocketDisconnect:
            pass
        finally:
            for task in (event_task, receive_task):
                task.cancel()
                with suppress(asyncio.CancelledError, WebSocketDisconnect):
                    await task
