from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status

from app.api.dependencies import authenticate_admin_request, get_container
from app.api.schemas.customer_experience import (
    CreateKnowledgeGapRequest,
    DeleteAutomationRuleRequest,
    ResolveKnowledgeGapRequest,
    SaveAutomationRuleRequest,
    SaveWidgetDraftRequest,
    TestAutomationRuleRequest,
    WidgetVersionActionRequest,
)
from app.application.dto import (
    AutomationTestResult,
    CreateKnowledgeGapCommand,
    DeleteAutomationRuleCommand,
    GetExperienceSummaryQuery,
    GetWidgetConfigurationQuery,
    ListAutomationExecutionsQuery,
    ListAutomationRulesQuery,
    ListKnowledgeGapsQuery,
    PublishWidgetVersionCommand,
    ResolveKnowledgeGapCommand,
    RollbackWidgetVersionCommand,
    SaveAutomationRuleCommand,
    SaveWidgetDraftCommand,
    TestAutomationRuleCommand,
)
from app.application.dto.widget_assets import ListWidgetAssetsQuery, UploadWidgetAssetCommand
from app.bootstrap.container import Container
from app.domain.models import (
    AutomationActions,
    AutomationConditions,
    AutomationFacts,
    WidgetConfig,
)

router = APIRouter(prefix="/v1/admin/customer-experience", tags=["customer-experience"])


@router.get("/sites/{site_id}/widget-assets")
async def list_widget_assets(
    site_id: str,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> dict:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await container.widget_asset_service.list_assets(
            ListWidgetAssetsQuery(principal=principal, site_id=site_id, limit=limit)
        )
    except (PermissionError, LookupError, ValueError) as exc:
        raise _http_error(exc) from exc
    return {"items": [_widget_asset(item) for item in result.assets]}


@router.post("/sites/{site_id}/widget-assets", status_code=status.HTTP_201_CREATED)
async def upload_widget_asset(
    site_id: str,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
    purpose: Annotated[str, Query(pattern="^(launcher|avatar)$")] = "launcher",
    idempotency_key: Annotated[str, Header(alias="X-Idempotency-Key", max_length=200)] = "",
) -> dict:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await container.widget_asset_service.upload(
            UploadWidgetAssetCommand(
                principal=principal,
                site_id=site_id,
                purpose=purpose,
                source_content_type=request.headers.get("content-type", ""),
                content=await request.body(),
                idempotency_key=idempotency_key,
            )
        )
    except (PermissionError, LookupError, RuntimeError, ValueError) as exc:
        raise _http_error(exc) from exc
    return _widget_asset(result.asset)


@router.get("/sites/{site_id}/widget-config")
async def get_widget_configuration(
    site_id: str,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> dict:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await container.customer_experience_service.get_widget_configuration(
            GetWidgetConfigurationQuery(principal, site_id)
        )
    except (PermissionError, LookupError, ValueError) as exc:
        raise _http_error(exc) from exc
    return _widget_state(result.widget_configuration)


@router.post("/sites/{site_id}/widget-config/drafts")
async def save_widget_draft(
    site_id: str,
    payload: SaveWidgetDraftRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> dict:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await container.customer_experience_service.save_widget_draft(
            SaveWidgetDraftCommand(
                principal=principal,
                site_id=site_id,
                config=_widget_config(payload),
                idempotency_key=payload.idempotency_key,
            )
        )
    except (PermissionError, LookupError, RuntimeError, ValueError) as exc:
        raise _http_error(exc) from exc
    return _widget_state(result.widget_configuration)


@router.post("/sites/{site_id}/widget-config/publish")
async def publish_widget_version(
    site_id: str,
    payload: WidgetVersionActionRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> dict:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await container.customer_experience_service.publish_widget_version(
            PublishWidgetVersionCommand(
                principal, site_id, payload.version_id, payload.idempotency_key
            )
        )
    except (PermissionError, LookupError, RuntimeError, ValueError) as exc:
        raise _http_error(exc) from exc
    return _widget_state(result.widget_configuration)


@router.post("/sites/{site_id}/widget-config/rollback")
async def rollback_widget_version(
    site_id: str,
    payload: WidgetVersionActionRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> dict:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await container.customer_experience_service.rollback_widget_version(
            RollbackWidgetVersionCommand(
                principal, site_id, payload.version_id, payload.idempotency_key
            )
        )
    except (PermissionError, LookupError, RuntimeError, ValueError) as exc:
        raise _http_error(exc) from exc
    return _widget_state(result.widget_configuration)


@router.get("/automation/rules")
async def list_automation_rules(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> dict:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await container.customer_experience_service.list_rules(
            ListAutomationRulesQuery(principal)
        )
    except PermissionError as exc:
        raise _http_error(exc) from exc
    return {"items": [_rule(item) for item in result.automation_rules]}


@router.post("/automation/rules")
async def save_automation_rule(
    payload: SaveAutomationRuleRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> dict:
    principal = await authenticate_admin_request(request, container)
    try:
        rule = await container.customer_experience_service.save_rule(
            SaveAutomationRuleCommand(
                principal=principal,
                rule_id=payload.rule_id,
                name=payload.name,
                enabled=payload.enabled,
                sort_order=payload.sort_order,
                conditions=AutomationConditions(**payload.conditions.model_dump()),
                actions=AutomationActions(
                    **{
                        **payload.actions.model_dump(),
                        "tags": tuple(payload.actions.tags),
                    }
                ),
                idempotency_key=payload.idempotency_key,
            )
        )
    except (PermissionError, LookupError, RuntimeError, ValueError) as exc:
        raise _http_error(exc) from exc
    return _rule(rule)


@router.delete("/automation/rules/{rule_id}")
async def delete_automation_rule(
    rule_id: str,
    payload: DeleteAutomationRuleRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> dict:
    principal = await authenticate_admin_request(request, container)
    try:
        await container.customer_experience_service.delete_rule(
            DeleteAutomationRuleCommand(principal, rule_id, payload.idempotency_key)
        )
    except (PermissionError, RuntimeError, ValueError) as exc:
        raise _http_error(exc) from exc
    return {"status": "deleted"}


@router.post("/automation/test")
async def test_automation_rule(
    payload: TestAutomationRuleRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> dict:
    principal = await authenticate_admin_request(request, container)
    try:
        result: AutomationTestResult = container.customer_experience_service.test_rule(
            TestAutomationRuleCommand(
                principal,
                AutomationConditions(**payload.conditions.model_dump()),
                AutomationFacts(**payload.facts.model_dump()),
            )
        )
    except (PermissionError, ValueError) as exc:
        raise _http_error(exc) from exc
    return {"matched": result.matched, "reasons": list(result.reasons)}


@router.get("/automation/executions")
async def list_automation_executions(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await container.customer_experience_service.list_executions(
            ListAutomationExecutionsQuery(principal, limit)
        )
    except (PermissionError, ValueError) as exc:
        raise _http_error(exc) from exc
    return {"items": [asdict(item) for item in result.automation_executions]}


@router.get("/knowledge-gaps")
async def list_knowledge_gaps(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
    gap_status: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> dict:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await container.customer_experience_service.list_knowledge_gaps(
            ListKnowledgeGapsQuery(principal, gap_status, limit)
        )
    except (PermissionError, ValueError) as exc:
        raise _http_error(exc) from exc
    return {"items": [asdict(item) for item in result.knowledge_gaps]}


@router.post("/conversations/{conversation_id}/knowledge-gaps")
async def create_knowledge_gap(
    conversation_id: str,
    payload: CreateKnowledgeGapRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> dict:
    principal = await authenticate_admin_request(request, container)
    try:
        gap = await container.customer_experience_service.create_knowledge_gap(
            CreateKnowledgeGapCommand(
                principal,
                conversation_id,
                payload.category,
                payload.summary,
                payload.idempotency_key,
            )
        )
    except (PermissionError, LookupError, RuntimeError, ValueError) as exc:
        raise _http_error(exc) from exc
    return asdict(gap)


@router.post("/knowledge-gaps/{gap_id}/resolve")
async def resolve_knowledge_gap(
    gap_id: str,
    payload: ResolveKnowledgeGapRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> dict:
    principal = await authenticate_admin_request(request, container)
    try:
        gap = await container.customer_experience_service.resolve_knowledge_gap(
            ResolveKnowledgeGapCommand(
                principal, gap_id, payload.resolution_note, payload.idempotency_key
            )
        )
    except (PermissionError, LookupError, RuntimeError, ValueError) as exc:
        raise _http_error(exc) from exc
    return asdict(gap)


@router.get("/summary")
async def customer_experience_summary(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
    days: Annotated[int, Query(ge=1, le=365)] = 30,
    site_id: str | None = None,
) -> dict:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await container.customer_experience_service.summary(
            GetExperienceSummaryQuery(principal, days, site_id)
        )
    except (PermissionError, ValueError) as exc:
        raise _http_error(exc) from exc
    return asdict(result.summary) if result.summary else {}


def _widget_config(payload: SaveWidgetDraftRequest) -> WidgetConfig:
    values = payload.model_dump(exclude={"idempotency_key"})
    values["holidays"] = tuple(values["holidays"])
    return WidgetConfig(**values)


def _widget_asset(item) -> dict:  # type: ignore[no-untyped-def]
    return {
        "asset_id": item.asset_id,
        "site_id": item.site_id,
        "purpose": item.purpose.value,
        "status": item.status.value,
        "url": f"/v1/widget-media/{item.asset_id}?size=256",
        "width": item.width,
        "height": item.height,
        "source_byte_size": item.source_byte_size,
        "created_at": item.created_at,
    }


def _widget_state(state) -> dict:  # type: ignore[no-untyped-def]
    if state is None:
        return {}
    return {
        "site_id": state.site_id,
        "published": _version(state.published),
        "draft": _version(state.draft),
        "versions": [_version(item) for item in state.versions],
    }


def _version(item) -> dict | None:  # type: ignore[no-untyped-def]
    if item is None:
        return None
    return {
        "version_id": item.version_id,
        "version_number": item.version_number,
        "status": item.status.value,
        "config": asdict(item.config),
        "created_by": item.created_by,
        "created_at": item.created_at,
        "published_at": item.published_at,
    }


def _rule(item) -> dict:  # type: ignore[no-untyped-def]
    return {
        "rule_id": item.rule_id,
        "name": item.name,
        "enabled": item.enabled,
        "sort_order": item.sort_order,
        "conditions": asdict(item.conditions),
        "actions": asdict(item.actions),
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PermissionError):
        code = status.HTTP_403_FORBIDDEN
    elif isinstance(exc, LookupError):
        code = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, RuntimeError):
        code = status.HTTP_409_CONFLICT
    else:
        code = status.HTTP_422_UNPROCESSABLE_ENTITY
    return HTTPException(status_code=code, detail=str(exc))
