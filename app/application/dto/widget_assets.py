from dataclasses import dataclass

from app.domain.models import AuthenticatedPrincipal
from app.domain.models.widget_asset import WidgetAsset


@dataclass(frozen=True, slots=True)
class UploadWidgetAssetCommand:
    principal: AuthenticatedPrincipal
    site_id: str
    purpose: str
    source_content_type: str
    content: bytes
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ListWidgetAssetsQuery:
    principal: AuthenticatedPrincipal
    site_id: str
    limit: int = 100


@dataclass(frozen=True, slots=True)
class ReadPublicWidgetAssetQuery:
    asset_id: str
    size: int


@dataclass(frozen=True, slots=True)
class WidgetAssetResult:
    asset: WidgetAsset


@dataclass(frozen=True, slots=True)
class WidgetAssetListResult:
    assets: tuple[WidgetAsset, ...]


@dataclass(frozen=True, slots=True)
class PublicWidgetAssetResult:
    content: bytes
    content_type: str = "image/webp"
