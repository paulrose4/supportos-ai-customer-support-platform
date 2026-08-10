from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status

from app.api.dependencies import authenticate_admin_request, get_container
from app.api.request_context import country_code, source_address, visitor_user_agent
from app.api.schemas.presence import (
    VisitorPresenceItem,
    VisitorPresenceListResponse,
    WidgetPresenceRequest,
    WidgetPresenceResponse,
)
from app.application.dto import ListVisitorPresenceQuery, RecordVisitorPresenceCommand
from app.application.dto.lead_scoring import LeadScoreResult
from app.bootstrap.container import Container
from app.domain.models import VisitorPresence

router = APIRouter(tags=["visitor-presence"])


@router.post("/v1/widget/presence", response_model=WidgetPresenceResponse)
async def record_widget_presence(
    payload: WidgetPresenceRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
    site_key: Annotated[str, Header(alias="X-Agent-Site-Key", min_length=8, max_length=256)],
) -> WidgetPresenceResponse:
    try:
        principal = await container.widget_authentication.authenticate_site(
            site_key=site_key,
            correlation_id=request.state.correlation_id,
        )
        presence = await container.visitor_presence_service.record(
            RecordVisitorPresenceCommand(
                principal=principal,
                visitor_id=payload.visitor_id,
                conversation_id=payload.conversation_id,
                page_path=payload.page_path,
                page_kind=payload.page_kind,
                page_title=payload.page_title,
                referrer=payload.referrer,
                source_address=source_address(
                    request,
                    trusted_forwarded_address=request.headers.get("x-agent-visitor-ip"),
                ),
                country_code=country_code(
                    request,
                    trusted_forwarded_country=request.headers.get("x-agent-visitor-country"),
                ),
                user_agent=visitor_user_agent(
                    request,
                    trusted_forwarded_user_agent=request.headers.get("x-agent-visitor-user-agent"),
                ),
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
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid widget site credential",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="invalid visitor presence",
        ) from exc
    return WidgetPresenceResponse(last_seen_at=presence.last_seen_at)


@router.get("/v1/admin/presence", response_model=VisitorPresenceListResponse)
async def list_visitor_presence(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
    active_within_seconds: Annotated[int, Query(ge=15, le=300)] = 60,
) -> VisitorPresenceListResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await container.visitor_presence_service.list_active(
            ListVisitorPresenceQuery(
                principal=principal,
                active_within_seconds=active_within_seconds,
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    scores = result.scores
    return VisitorPresenceListResponse(
        items=[
            _presence_item(item, score) for item, score in zip(result.items, scores, strict=True)
        ]
    )


def _presence_item(item: VisitorPresence, score: LeadScoreResult) -> VisitorPresenceItem:
    """Map the domain presence and canonical score into one public projection."""

    return VisitorPresenceItem(
        site_id=item.site_id,
        visitor_id=item.visitor_id,
        conversation_id=item.conversation_id,
        page_path=item.page_path,
        page_kind=(score.page_kind.value if score.page_kind is not None else item.page_kind),
        last_seen_at=item.last_seen_at,
        first_seen_at=item.first_seen_at or item.last_seen_at,
        page_title=item.page_title,
        referrer=item.referrer,
        ip_address=item.ip_address,
        country_code=item.country_code,
        browser=item.browser,
        operating_system=item.operating_system,
        device_type=item.device_type,
        language=item.language,
        timezone=item.timezone,
        page_view_count=item.page_view_count,
        session_started_at=(item.session_started_at or item.first_seen_at or item.last_seen_at),
        current_page_entered_at=item.current_page_entered_at or item.last_seen_at,
        last_page_view_id=item.last_page_view_id,
        widget_state=item.widget_state,
        presence_source=item.presence_source,
        runtime_version=item.runtime_version,
        config_version=item.config_version,
        connector_type=item.connector_type,
        connector_version=item.connector_version,
        commercial_intent=score.commercial_intent,
        intent_tier=score.tier.value,
        operation_priority=score.operation_priority.value,
        confidence=score.confidence,
        confidence_grade=score.confidence_grade,
        queue_eligible=score.queue_eligible,
        next_action=score.next_action.value,
        signals=list(score.signals),
        freshness=score.freshness.value,
        rule_version=score.rule_version,
        scored_at=score.scored_at,
        current_page_dwell_seconds=score.current_page_dwell_seconds,
        session_active_dwell_seconds=score.session_active_dwell_seconds,
        session_age_seconds=score.session_age_seconds,
        data_coverage=list(score.data_coverage),
    )
