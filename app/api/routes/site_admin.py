from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.dependencies import authenticate_admin_request, get_container
from app.api.routes.widget_assets import widget_asset_version
from app.api.schemas.site_admin import (
    ManagedSiteCreateRequest,
    ManagedSiteKeyRotationRequest,
    ManagedSiteListResponse,
    ManagedSiteResponse,
    ManagedSiteUpdateRequest,
    SiteVerificationChallengeRequest,
    SiteVerificationChallengeResponse,
    SiteVerificationRequest,
    SiteWebSourceResponse,
    SiteWebSourceUpdateRequest,
)
from app.application.dto import (
    CompleteSiteVerificationCommand,
    CreateManagedSiteCommand,
    GetSiteWebSourceConfigQuery,
    IssueSiteVerificationChallengeCommand,
    ListManagedSitesQuery,
    RotateManagedSiteKeyCommand,
    SiteWebSourceConfigResult,
    UpdateManagedSiteCommand,
    UpdateSiteWebSourceConfigCommand,
)
from app.application.services import (
    SiteAdministrationConflictError,
    SiteWebSourceConfigConflictError,
)
from app.bootstrap.container import Container

router = APIRouter(prefix="/v1/admin/site-management", tags=["site-management"])


@router.get("/{site_id}/web-source", response_model=SiteWebSourceResponse)
async def get_site_web_source(
    site_id: str,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> SiteWebSourceResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await container.site_web_source_config_service.get_config(
            GetSiteWebSourceConfigQuery(principal=principal, site_id=site_id)
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return _web_source_response(result)


@router.put("/{site_id}/web-source", response_model=SiteWebSourceResponse)
async def update_site_web_source(
    site_id: str,
    payload: SiteWebSourceUpdateRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> SiteWebSourceResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await container.site_web_source_config_service.update_config(
            UpdateSiteWebSourceConfigCommand(
                principal=principal,
                site_id=site_id,
                discovery_mode=payload.discovery_mode,
                explicit_sitemap_urls=tuple(payload.explicit_sitemap_urls),
                expected_config_version=payload.expected_config_version,
                correlation_id=request.state.correlation_id,
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except SiteWebSourceConfigConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return _web_source_response(result)


@router.get("", response_model=ManagedSiteListResponse)
async def list_managed_sites(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> ManagedSiteListResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        result = await container.site_administration_service.list_sites(
            ListManagedSitesQuery(principal)
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return ManagedSiteListResponse(
        items=[_response(item, container.settings.public_widget_base_url) for item in result.items]
    )


@router.post("", response_model=ManagedSiteResponse, status_code=status.HTTP_201_CREATED)
async def create_managed_site(
    payload: ManagedSiteCreateRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> ManagedSiteResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        site = await container.site_administration_service.create_site(
            CreateManagedSiteCommand(
                principal=principal,
                site_id=payload.site_id,
                name=payload.name,
                base_url=payload.base_url,
                site_key=payload.site_key,
                correlation_id=request.state.correlation_id,
                primary_language=payload.primary_language,
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except SiteAdministrationConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return _response(site, container.settings.public_widget_base_url)


@router.put("/{site_id}", response_model=ManagedSiteResponse)
async def update_managed_site(
    site_id: str,
    payload: ManagedSiteUpdateRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> ManagedSiteResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        site = await container.site_administration_service.update_site(
            UpdateManagedSiteCommand(
                principal=principal,
                site_id=site_id,
                name=payload.name,
                base_url=payload.base_url,
                status=payload.status,
                correlation_id=request.state.correlation_id,
                primary_language=payload.primary_language,
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
    return _response(site, container.settings.public_widget_base_url)


@router.post(
    "/{site_id}/verification/challenge",
    response_model=SiteVerificationChallengeResponse,
)
async def issue_site_verification_challenge(
    site_id: str,
    payload: SiteVerificationChallengeRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> SiteVerificationChallengeResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        challenge = await container.site_administration_service.issue_verification_challenge(
            IssueSiteVerificationChallengeCommand(
                principal=principal,
                site_id=site_id,
                method=payload.method,
                correlation_id=request.state.correlation_id,
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
    return SiteVerificationChallengeResponse(
        site_id=challenge.site_id,
        method=challenge.method,
        dns_name=challenge.dns_name,
        dns_value=challenge.dns_value,
        script_path=challenge.script_path,
        script_value=challenge.script_value,
        expires_at=challenge.expires_at,
        verification_status=challenge.verification_status,
    )


@router.post("/{site_id}/verification/verify", response_model=ManagedSiteResponse)
async def verify_managed_site(
    site_id: str,
    payload: SiteVerificationRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> ManagedSiteResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        site = await container.site_administration_service.verify_site(
            CompleteSiteVerificationCommand(
                principal=principal,
                site_id=site_id,
                method=payload.method,
                correlation_id=request.state.correlation_id,
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _response(site, container.settings.public_widget_base_url)


@router.post("/{site_id}/rotate-key", response_model=ManagedSiteResponse)
async def rotate_managed_site_key(
    site_id: str,
    payload: ManagedSiteKeyRotationRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> ManagedSiteResponse:
    principal = await authenticate_admin_request(request, container)
    try:
        site = await container.site_administration_service.rotate_key(
            RotateManagedSiteKeyCommand(
                principal=principal,
                site_id=site_id,
                site_key=payload.site_key,
                correlation_id=request.state.correlation_id,
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _response(site, container.settings.public_widget_base_url)


def _response(site, public_widget_base_url: str) -> ManagedSiteResponse:  # type: ignore[no-untyped-def]
    script_url = public_widget_base_url.rstrip("/") + f"/widget.js?v={widget_asset_version()}"
    return ManagedSiteResponse(
        site_id=site.site_id,
        public_widget_id=site.public_widget_id,
        name=site.name,
        base_url=site.base_url,
        allowed_origins=list(site.allowed_origins),
        widget_daily_message_limit=site.widget_daily_message_limit,
        primary_language=site.primary_language,
        install_code=(
            f'<script async src="{script_url}" data-site-id="{site.public_widget_id}" '
            f'data-language="{site.primary_language}" data-presence-mode="page_view"></script>'
        ),
        status=site.status,
        credential_key_prefix=site.credential_key_prefix,
        credential_status=site.credential_status,
        verification_status=site.verification_status,
        verification_method=site.verification_method,
        verification_token_prefix=site.verification_token_prefix,
        verification_expires_at=(
            site.verification_expires_at.isoformat()
            if site.verification_expires_at is not None
            else None
        ),
        verified_at=site.verified_at.isoformat() if site.verified_at is not None else None,
        created_at=site.created_at.isoformat(),
        updated_at=site.updated_at.isoformat(),
    )


def _web_source_response(result: SiteWebSourceConfigResult) -> SiteWebSourceResponse:
    config = result.config
    return SiteWebSourceResponse(
        site_id=config.site_id,
        discovery_mode=config.discovery_mode.value,
        explicit_sitemap_urls=list(config.explicit_sitemap_urls),
        allowed_sitemap_origins=list(result.allowed_sitemap_origins),
        config_version=config.config_version,
        validation_status=config.validation_status.value,
        validated_at=config.validated_at.isoformat() if config.validated_at is not None else None,
        updated_by=config.updated_by,
        updated_at=config.updated_at.isoformat() if config.updated_at is not None else None,
    )
