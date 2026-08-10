import secrets

from fastapi import APIRouter, Header, Request
from fastapi.responses import Response

router = APIRouter(tags=["observability"])


@router.get("/metrics", include_in_schema=False)
async def prometheus_metrics(
    request: Request,
    authorization: str | None = Header(default=None),
) -> Response:
    container = request.app.state.container
    if not container.settings.prometheus_metrics_enabled:
        return Response(status_code=404)
    expected_secret = container.settings.prometheus_metrics_token
    expected = expected_secret.get_secret_value() if expected_secret is not None else ""
    supplied = authorization.removeprefix("Bearer ") if authorization else ""
    if expected and not secrets.compare_digest(supplied, expected):
        return Response(status_code=401)
    render = getattr(container.request_metrics, "render", None)
    if render is None:
        return Response(status_code=503)
    return Response(content=render(), media_type="text/plain; version=0.0.4; charset=utf-8")
