from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.dependencies import authenticate_admin_request, get_container
from app.api.schemas.analytics import RouteLatencyResponse, SupportAnalyticsResponse
from app.application.dto import GetSupportAnalyticsQuery
from app.bootstrap.container import Container

router = APIRouter(prefix="/v1/admin/analytics", tags=["support-analytics"])


@router.get("/overview", response_model=SupportAnalyticsResponse)
async def analytics_overview(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
    days: Annotated[int, Query(ge=1, le=365)] = 30,
    site_id: str | None = None,
) -> SupportAnalyticsResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await container.support_analytics_service.overview(
            GetSupportAnalyticsQuery(principal=principal, days=days, site_id=site_id)
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    item = result.snapshot
    runs = max(item.agent_runs, 1)
    conversations = max(item.conversations, 1)
    auto_resolution_denominator = max(item.auto_resolution_eligible_conversations, 1)
    handoff_context_denominator = max(item.handoff_context_count, 1)
    eligible_runs = max(item.ai_eligible_runs, 1)
    return SupportAnalyticsResponse(
        days=item.days,
        site_id=item.site_id,
        conversations=item.conversations,
        agent_runs=item.agent_runs,
        ai_answers=item.ai_answers,
        handoffs=item.handoffs,
        human_replied_conversations=item.human_replied_conversations,
        resolved_conversations=item.resolved_conversations,
        ai_answer_rate=round(item.ai_answers / runs, 4),
        handoff_rate=round(item.handoffs / runs, 4),
        human_reply_rate=round(item.human_replied_conversations / conversations, 4),
        resolution_rate=round(item.resolved_conversations / conversations, 4),
        average_first_response_seconds=round(item.average_first_response_seconds, 2),
        average_human_response_seconds=round(item.average_human_response_seconds, 2),
        average_resolution_seconds=round(item.average_resolution_seconds, 2),
        unread_conversations=item.unread_conversations,
        waiting_human_conversations=item.waiting_human_conversations,
        latency_sample_count=item.latency_sample_count,
        first_response_p50_seconds=round(item.first_response_p50_seconds, 3),
        first_response_p95_seconds=round(item.first_response_p95_seconds, 3),
        first_response_p99_seconds=round(item.first_response_p99_seconds, 3),
        latency_by_route=[
            RouteLatencyResponse(
                route=value.route,
                sample_count=value.sample_count,
                p50_seconds=round(value.p50_seconds, 3),
                p95_seconds=round(value.p95_seconds, 3),
                p99_seconds=round(value.p99_seconds, 3),
            )
            for value in item.latency_by_route
        ],
        auto_resolution_eligible_conversations=item.auto_resolution_eligible_conversations,
        auto_resolved_conversations=item.auto_resolved_conversations,
        auto_resolution_rate=round(
            item.auto_resolved_conversations / auto_resolution_denominator, 4
        ),
        reopened_conversations=item.reopened_conversations,
        handoff_context_count=item.handoff_context_count,
        complete_handoff_context_count=item.complete_handoff_context_count,
        handoff_context_completeness_rate=round(
            item.complete_handoff_context_count / handoff_context_denominator, 4
        ),
        legacy_handoff_context_count=item.legacy_handoff_context_count,
        ai_eligible_runs=item.ai_eligible_runs,
        forced_order_handoffs=item.forced_order_handoffs,
        eligible_ai_answer_rate=round(item.ai_answers / eligible_runs, 4),
    )
