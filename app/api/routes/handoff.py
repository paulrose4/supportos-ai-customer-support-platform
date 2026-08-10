from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.dependencies import authenticate_admin_request, get_container
from app.api.schemas import HandoffQueueItem, HandoffQueueResponse
from app.application.dto import ListHandoffsQuery
from app.bootstrap.container import Container

router = APIRouter(prefix="/v1/handoffs", tags=["handoffs"])


@router.get("", response_model=HandoffQueueResponse)
async def list_handoffs(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
    handoff_status: Annotated[str | None, Query(alias="status")] = "pending",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: str | None = None,
    site_id: str | None = None,
    assigned_agent_id: str | None = None,
) -> HandoffQueueResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await container.list_handoffs_service.execute(
            ListHandoffsQuery(
                principal=principal,
                status=handoff_status or None,
                limit=limit,
                cursor=cursor,
                site_id=site_id,
                assigned_agent_id=assigned_agent_id,
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return HandoffQueueResponse(
        items=[
            HandoffQueueItem(
                handoff_id=item.handoff_id,
                conversation_id=item.conversation_id,
                customer_id=item.customer_id,
                reason_code=item.reason_code,
                risk_level=item.risk_level,
                summary=item.summary,
                status=item.status.value,
                priority=item.priority,
                queue_id=item.queue_id,
                assigned_agent_id=item.assigned_agent_id,
                accepted_at=item.accepted_at,
                resolved_at=item.resolved_at,
                failed_tools=list(item.failed_tools),
                knowledge_sources=list(item.knowledge_sources),
                user_intent=item.user_intent,
                unresolved_question=item.unresolved_question,
                ai_attempt=item.ai_attempt,
                suggested_next_action=item.suggested_next_action,
                reply_draft=item.reply_draft,
                created_at=item.created_at,
                context_schema_version=item.context_schema_version,
                customer_language=item.customer_language,
                customer_region=item.customer_region,
                identity_status=item.identity_status,
                product_ids=list(item.product_ids),
                order_id=item.order_id,
                confirmed_fields=list(item.confirmed_fields),
                customer_sentiment=item.customer_sentiment,
                customer_request=item.customer_request,
                commitment_deadline=item.commitment_deadline,
            )
            for item in result.handoffs
        ],
        next_cursor=result.next_cursor,
    )
