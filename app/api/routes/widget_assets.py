from functools import lru_cache
from hashlib import sha256
from pathlib import Path

from fastapi import APIRouter, Response

router = APIRouter(tags=["public-widget-assets"])
_ASSET_ROOT = Path(__file__).resolve().parents[1] / "assets"


@router.get("/widget.js", include_in_schema=False)
async def widget_javascript(v: str | None = None) -> Response:
    return Response(
        content=_asset("widget.js"),
        media_type="application/javascript",
        headers={"Cache-Control": _cache_control(v), "X-Content-Type-Options": "nosniff"},
    )


@router.get("/widget.css", include_in_schema=False)
async def widget_stylesheet(v: str | None = None) -> Response:
    return Response(
        content=_asset("widget.css"),
        media_type="text/css",
        headers={"Cache-Control": _cache_control(v), "X-Content-Type-Options": "nosniff"},
    )


@router.get("/widget-runtime.js", include_in_schema=False)
async def widget_runtime_javascript(v: str | None = None) -> Response:
    return Response(
        content=_asset("widget-runtime.js"),
        media_type="application/javascript",
        headers={"Cache-Control": _cache_control(v), "X-Content-Type-Options": "nosniff"},
    )


@lru_cache
def _asset(name: str) -> str:
    return (_ASSET_ROOT / name).read_text(encoding="utf-8")


@lru_cache
def widget_asset_version() -> str:
    digest = sha256()
    for name in ("widget.js", "widget-runtime.js", "widget.css"):
        digest.update(name.encode("ascii"))
        digest.update((_ASSET_ROOT / name).read_bytes())
    return digest.hexdigest()[:16]


def _cache_control(version: str | None) -> str:
    if version == widget_asset_version():
        return "public, max-age=31536000, immutable"
    return "no-cache"
