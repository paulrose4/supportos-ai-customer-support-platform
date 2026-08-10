from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.api.dependencies import get_container
from app.application.dto.widget_assets import ReadPublicWidgetAssetQuery
from app.bootstrap.container import Container

router = APIRouter(tags=["public-widget-media"])


@router.get("/v1/widget-media/{asset_id}", include_in_schema=False)
async def widget_image(
    asset_id: str,
    container: Annotated[Container, Depends(get_container)],
    size: Annotated[int, Query()] = 128,
) -> Response:
    try:
        result = await container.widget_asset_service.read_public(
            ReadPublicWidgetAssetQuery(asset_id=asset_id, size=size)
        )
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="widget image was not found") from exc
    return Response(
        content=result.content,
        media_type=result.content_type,
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline",
        },
    )
