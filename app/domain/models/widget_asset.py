from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class WidgetAssetPurpose(StrEnum):
    LAUNCHER = "launcher"
    AVATAR = "avatar"


class WidgetAssetStatus(StrEnum):
    ACTIVE = "active"
    RETIRED = "retired"


@dataclass(frozen=True, slots=True)
class WidgetAsset:
    asset_id: str
    tenant_id: str
    site_id: str
    purpose: WidgetAssetPurpose
    status: WidgetAssetStatus
    content_hash: str
    source_content_type: str
    source_byte_size: int
    width: int
    height: int
    created_by: str
    created_at: datetime
    retired_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class WidgetAssetVariant:
    size: int
    content: bytes
    content_type: str = "image/webp"
