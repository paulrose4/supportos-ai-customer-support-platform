from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class SiteWebDiscoveryMode(StrEnum):
    AUTO = "auto"
    HYBRID = "hybrid"
    MANUAL = "manual"


class SiteWebSourceValidationStatus(StrEnum):
    UNVALIDATED = "unvalidated"
    VALID = "valid"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class SiteWebSourceConfig:
    tenant_id: str
    site_id: str
    discovery_mode: SiteWebDiscoveryMode
    explicit_sitemap_urls: tuple[str, ...]
    config_version: int
    validation_status: SiteWebSourceValidationStatus
    validated_at: datetime | None
    updated_by: str | None
    updated_at: datetime | None
