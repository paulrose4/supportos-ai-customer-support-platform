import ipaddress
import re
from datetime import UTC, datetime
from urllib.parse import SplitResult, urlsplit, urlunsplit

from app.application.dto.site_web_source import (
    GetSiteWebSourceConfigQuery,
    SiteWebSourceConfigResult,
    UpdateSiteWebSourceConfigCommand,
)
from app.domain.models import AuthenticatedPrincipal
from app.domain.models.site_web_source import (
    SiteWebDiscoveryMode,
    SiteWebSourceConfig,
    SiteWebSourceValidationStatus,
)
from app.domain.ports.site_web_source import (
    SiteWebSourceConfigStorePort,
    SiteWebSourceConfigVersionConflictError,
)

_SITE_ID_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_MAX_SITEMAP_URLS = 10
_MAX_SITEMAP_URL_LENGTH = 2048


class SiteWebSourceConfigConflictError(RuntimeError):
    pass


class SiteWebSourceConfigService:
    def __init__(self, store: SiteWebSourceConfigStorePort) -> None:
        self._store = store

    async def get_config(
        self,
        query: GetSiteWebSourceConfigQuery,
    ) -> SiteWebSourceConfigResult:
        _require_read_sites(query.principal.scopes)
        site_id = _validate_site_id(query.site_id)
        base_url = await self._store.get_site_base_url(
            tenant_id=query.principal.tenant_id,
            site_id=site_id,
        )
        if base_url is None:
            raise LookupError("site was not found")
        config = await self._store.get_config(
            tenant_id=query.principal.tenant_id,
            site_id=site_id,
        )
        if config is None:
            config = SiteWebSourceConfig(
                tenant_id=query.principal.tenant_id,
                site_id=site_id,
                discovery_mode=SiteWebDiscoveryMode.HYBRID,
                explicit_sitemap_urls=(),
                config_version=0,
                validation_status=SiteWebSourceValidationStatus.UNVALIDATED,
                validated_at=None,
                updated_by=None,
                updated_at=None,
            )
        return SiteWebSourceConfigResult(
            config,
            await self._allowed_sitemap_origins(
                tenant_id=query.principal.tenant_id,
                base_url=base_url,
            ),
        )

    async def update_config(
        self,
        command: UpdateSiteWebSourceConfigCommand,
    ) -> SiteWebSourceConfigResult:
        _require_manage_sites(command.principal.scopes)
        site_id = _validate_site_id(command.site_id)
        try:
            discovery_mode = SiteWebDiscoveryMode(command.discovery_mode.strip().casefold())
        except ValueError as exc:
            raise ValueError("discovery_mode must be auto, hybrid, or manual") from exc
        base_url = await self._store.get_site_base_url(
            tenant_id=command.principal.tenant_id,
            site_id=site_id,
        )
        if base_url is None:
            raise LookupError("site was not found")
        if command.expected_config_version < 0:
            raise ValueError("expected_config_version must not be negative")
        allowed_sitemap_origins = await self._allowed_sitemap_origins(
            tenant_id=command.principal.tenant_id,
            base_url=base_url,
        )
        explicit_sitemap_urls = normalize_explicit_sitemap_urls(
            command.explicit_sitemap_urls,
            base_url=base_url,
            allowed_sitemap_origins=allowed_sitemap_origins,
        )
        if discovery_mode is SiteWebDiscoveryMode.MANUAL and not explicit_sitemap_urls:
            raise ValueError("manual discovery requires at least one sitemap URL")
        try:
            config = await self._store.put_config(
                tenant_id=command.principal.tenant_id,
                site_id=site_id,
                expected_base_url=base_url,
                expected_config_version=command.expected_config_version,
                discovery_mode=discovery_mode,
                explicit_sitemap_urls=explicit_sitemap_urls,
                actor_subject_id=command.principal.subject_id,
                correlation_id=command.correlation_id,
                changed_at=datetime.now(UTC),
            )
        except SiteWebSourceConfigVersionConflictError as exc:
            raise SiteWebSourceConfigConflictError(
                "site web source configuration changed; reload before saving"
            ) from exc
        if config is None:
            raise LookupError("site was not found")
        return SiteWebSourceConfigResult(config, allowed_sitemap_origins)

    async def _allowed_sitemap_origins(
        self,
        *,
        tenant_id: str,
        base_url: str,
    ) -> tuple[str, ...]:
        verified_base_urls = await self._store.list_active_verified_site_base_urls(
            tenant_id=tenant_id
        )
        origins = {
            _normalized_origin(urlsplit(value), field_name="verified site base URL")
            for value in verified_base_urls
        }
        origins.add(_normalized_origin(urlsplit(base_url), field_name="site base URL"))
        return tuple(sorted(origins))

    async def mark_validation_status(
        self,
        *,
        principal: AuthenticatedPrincipal,
        site_id: str,
        expected_config_version: int,
        validation_status: SiteWebSourceValidationStatus,
        validated_at: datetime | None,
    ) -> SiteWebSourceConfig | None:
        _require_validation_write(principal.scopes)
        normalized_site_id = _validate_site_id(site_id)
        if expected_config_version < 1:
            raise ValueError("expected_config_version must be positive")
        if validation_status is SiteWebSourceValidationStatus.UNVALIDATED:
            validated_at = None
        elif validated_at is None:
            raise ValueError("validated_at is required for a completed validation")
        return await self._store.mark_validation_status(
            tenant_id=principal.tenant_id,
            site_id=normalized_site_id,
            expected_config_version=expected_config_version,
            validation_status=validation_status,
            validated_at=validated_at,
            actor_subject_id=principal.subject_id,
            correlation_id=principal.correlation_id,
            changed_at=datetime.now(UTC),
        )


def normalize_explicit_sitemap_urls(
    urls: tuple[str, ...],
    *,
    base_url: str,
    allowed_sitemap_origins: tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    if len(urls) > _MAX_SITEMAP_URLS:
        raise ValueError(f"at most {_MAX_SITEMAP_URLS} sitemap URLs may be configured")
    expected_origin = _normalized_origin(urlsplit(base_url), field_name="site base URL")
    allowed_origins = frozenset(allowed_sitemap_origins or (expected_origin,))
    if expected_origin not in allowed_origins:
        raise ValueError("the registered site's origin must remain allowed")
    normalized_urls: list[str] = []
    seen: set[str] = set()
    for value in urls:
        normalized = _normalize_sitemap_url(value, allowed_origins=allowed_origins)
        if normalized not in seen:
            seen.add(normalized)
            normalized_urls.append(normalized)
    return tuple(normalized_urls)


def _normalize_sitemap_url(value: str, *, allowed_origins: frozenset[str]) -> str:
    candidate = value.strip()
    if not candidate:
        raise ValueError("sitemap URLs must not be empty")
    if len(candidate) > _MAX_SITEMAP_URL_LENGTH:
        raise ValueError(f"sitemap URLs must contain at most {_MAX_SITEMAP_URL_LENGTH} characters")
    if any(character.isspace() or ord(character) < 32 for character in candidate):
        raise ValueError("sitemap URLs must not contain whitespace or control characters")
    try:
        parsed = urlsplit(candidate)
    except ValueError as exc:
        raise ValueError("sitemap URL is invalid") from exc
    origin = _normalized_origin(parsed, field_name="sitemap URL")
    if origin not in allowed_origins:
        raise ValueError(
            "sitemap URL origin must match this site or another active, verified site "
            "in the same tenant"
        )
    if parsed.fragment:
        raise ValueError("sitemap URLs must not contain a fragment")
    path = parsed.path or "/"
    return urlunsplit(("https", origin.removeprefix("https://"), path, parsed.query, ""))


def _normalized_origin(parsed: SplitResult, *, field_name: str) -> str:
    if parsed.scheme.casefold() != "https" or not parsed.netloc:
        raise ValueError(f"{field_name} must be an absolute HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{field_name} must not contain credentials")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{field_name} contains an invalid port") from exc
    if port not in {None, 443}:
        raise ValueError(f"{field_name} must use the default HTTPS port")
    hostname = (parsed.hostname or "").casefold()
    if not hostname or any(ord(character) > 127 for character in hostname):
        raise ValueError(f"{field_name} hostname must use ASCII DNS labels")
    if hostname in {"localhost", "localhost.localdomain"}:
        raise ValueError(f"{field_name} must not target a local hostname")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError(f"{field_name} must not target a private or reserved address")
    authority = f"[{hostname}]" if address is not None and address.version == 6 else hostname
    return f"https://{authority}"


def _validate_site_id(site_id: str) -> str:
    normalized = site_id.strip().casefold()
    if not _SITE_ID_PATTERN.fullmatch(normalized):
        raise ValueError("site_id must be a lowercase DNS-style identifier")
    return normalized


def _require_read_sites(scopes: frozenset[str]) -> None:
    if not scopes.intersection({"sites:read", "sites:manage"}):
        raise PermissionError("site read permission is required")


def _require_manage_sites(scopes: frozenset[str]) -> None:
    if "sites:manage" not in scopes:
        raise PermissionError("site management permission is required")


def _require_validation_write(scopes: frozenset[str]) -> None:
    if not scopes.intersection({"knowledge:sync", "sites:manage"}):
        raise PermissionError("knowledge sync or site management permission is required")
