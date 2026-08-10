from dataclasses import dataclass

from app.domain.models import AuthenticatedPrincipal, SiteWebSourceConfig


@dataclass(frozen=True, slots=True)
class GetSiteWebSourceConfigQuery:
    principal: AuthenticatedPrincipal
    site_id: str


@dataclass(frozen=True, slots=True)
class UpdateSiteWebSourceConfigCommand:
    principal: AuthenticatedPrincipal
    site_id: str
    discovery_mode: str
    explicit_sitemap_urls: tuple[str, ...]
    correlation_id: str
    expected_config_version: int = 0


@dataclass(frozen=True, slots=True)
class SiteWebSourceConfigResult:
    config: SiteWebSourceConfig
    allowed_sitemap_origins: tuple[str, ...] = ()
