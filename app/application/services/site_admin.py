import hashlib
import hmac
import ipaddress
import re
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe
from urllib.parse import urlparse

from app.application.dto.site_admin import (
    CompleteSiteVerificationCommand,
    CreateManagedSiteCommand,
    IssueSiteVerificationChallengeCommand,
    ListManagedSitesQuery,
    ListManagedSitesResult,
    RotateManagedSiteKeyCommand,
    SiteVerificationChallenge,
    UpdateManagedSiteCommand,
)
from app.domain.models import AuthenticatedPrincipal, ManagedSupportSite
from app.domain.ports import SiteAdministrationPort, SiteVerificationProbePort

_SITE_ID_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_LANGUAGE_PATTERN = re.compile(r"^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$")


class SiteAdministrationConflictError(ValueError):
    pass


class SiteAdministrationService:
    def __init__(
        self,
        port: SiteAdministrationPort,
        *,
        default_daily_message_limit: int = 500,
        verification_token_secret: str = "development-site-verification-secret",
        verification_ttl_seconds: int = 3600,
        verification_probe: SiteVerificationProbePort | None = None,
    ) -> None:
        self._port = port
        self._default_daily_message_limit = default_daily_message_limit
        self._verification_token_secret = verification_token_secret.encode("utf-8")
        self._verification_ttl_seconds = verification_ttl_seconds
        self._verification_probe = verification_probe

    async def list_sites(self, query: ListManagedSitesQuery) -> ListManagedSitesResult:
        _require_manage_sites(query.principal.scopes)
        items = await self._port.list_managed_sites(tenant_id=query.principal.tenant_id)
        return ListManagedSitesResult(tuple(items))

    async def connector_manifest(self, principal: AuthenticatedPrincipal) -> ManagedSupportSite:
        if principal.authentication_method != "widget_site_key" or principal.site_id is None:
            raise PermissionError("a trusted widget site credential is required")
        site = await self._get_managed_site(
            tenant_id=principal.tenant_id,
            site_id=principal.site_id,
        )
        if site is None or site.status != "active" or site.verification_status != "verified":
            raise PermissionError("the widget site is unavailable")
        return site

    async def create_site(self, command: CreateManagedSiteCommand) -> ManagedSupportSite:
        _require_manage_sites(command.principal.scopes)
        site_id = _validate_site_id(command.site_id)
        name = _validate_name(command.name)
        base_url = _validate_base_url(command.base_url)
        primary_language = _validate_primary_language(command.primary_language)
        if not base_url:
            raise ValueError("base_url is required for public widget installation")
        key_hash, key_prefix = (
            _site_key_projection(command.site_key) if command.site_key is not None else (None, None)
        )
        site = await self._port.create_managed_site(
            tenant_id=command.principal.tenant_id,
            site_id=site_id,
            public_widget_id=f"site_pub_{token_urlsafe(18)}",
            name=name,
            base_url=base_url,
            allowed_origins=_allowed_origins(base_url),
            widget_daily_message_limit=self._default_daily_message_limit,
            primary_language=primary_language,
            key_hash=key_hash,
            key_prefix=key_prefix,
            actor_subject_id=command.principal.subject_id,
            correlation_id=command.correlation_id,
            created_at=datetime.now(UTC),
        )
        if site is None:
            raise SiteAdministrationConflictError(
                "site identifier already exists with different settings"
            )
        return site

    async def update_site(self, command: UpdateManagedSiteCommand) -> ManagedSupportSite:
        _require_manage_sites(command.principal.scopes)
        status = command.status.strip().casefold()
        if status not in {"active", "disabled"}:
            raise ValueError("site status must be active or disabled")
        base_url = _validate_base_url(command.base_url)
        primary_language = _validate_primary_language(command.primary_language)
        if not base_url:
            raise ValueError("base_url is required for public widget installation")
        site = await self._port.update_managed_site(
            tenant_id=command.principal.tenant_id,
            site_id=_validate_site_id(command.site_id),
            name=_validate_name(command.name),
            base_url=base_url,
            allowed_origins=_allowed_origins(base_url),
            status=status,
            primary_language=primary_language,
            actor_subject_id=command.principal.subject_id,
            correlation_id=command.correlation_id,
            changed_at=datetime.now(UTC),
        )
        if site is None:
            raise LookupError("site was not found")
        return site

    async def rotate_key(self, command: RotateManagedSiteKeyCommand) -> ManagedSupportSite:
        _require_manage_sites(command.principal.scopes)
        key_hash, key_prefix = _site_key_projection(command.site_key)
        site = await self._port.rotate_site_key(
            tenant_id=command.principal.tenant_id,
            site_id=_validate_site_id(command.site_id),
            key_hash=key_hash,
            key_prefix=key_prefix,
            actor_subject_id=command.principal.subject_id,
            correlation_id=command.correlation_id,
            changed_at=datetime.now(UTC),
        )
        if site is None:
            raise LookupError("site was not found")
        return site

    async def issue_verification_challenge(
        self, command: IssueSiteVerificationChallengeCommand
    ) -> SiteVerificationChallenge:
        _require_manage_sites(command.principal.scopes)
        method = command.method.strip().casefold()
        if method not in {"dns_txt", "script"}:
            raise ValueError("verification method must be dns_txt or script")
        site_id = _validate_site_id(command.site_id)
        site = await self._get_managed_site(
            tenant_id=command.principal.tenant_id,
            site_id=site_id,
        )
        if site is None:
            raise LookupError("site was not found")
        token = token_urlsafe(32)
        expires_at = datetime.now(UTC).replace(microsecond=0) + timedelta(
            seconds=self._verification_ttl_seconds
        )
        token_hash = _verification_token_hash(token, self._verification_token_secret)
        updated = await self._port.issue_verification_challenge(
            tenant_id=command.principal.tenant_id,
            site_id=site_id,
            method=method,
            token_hash=token_hash,
            token_prefix=token[:8],
            expires_at=expires_at,
            changed_at=datetime.now(UTC),
            actor_subject_id=command.principal.subject_id,
            correlation_id=command.correlation_id,
        )
        if updated is None:
            raise LookupError("site was not found")
        hostname = urlparse(updated.base_url).hostname or ""
        return SiteVerificationChallenge(
            site_id=updated.site_id,
            method=method,
            dns_name=f"_managed-support-verify.{hostname}",
            dns_value=token,
            script_path="/.well-known/managed-support-verification.txt",
            script_value=token,
            expires_at=expires_at.isoformat(),
            verification_status=updated.verification_status,
        )

    async def verify_site(self, command: CompleteSiteVerificationCommand) -> ManagedSupportSite:
        _require_manage_sites(command.principal.scopes)
        method = command.method.strip().casefold()
        if method not in {"dns_txt", "script"}:
            raise ValueError("verification method must be dns_txt or script")
        site_id = _validate_site_id(command.site_id)
        managed_site = await self._get_managed_site(
            tenant_id=command.principal.tenant_id,
            site_id=site_id,
        )
        if managed_site is None:
            raise LookupError("site was not found")
        proofs = []
        if method == "dns_txt":
            if self._verification_probe is not None:
                proofs.extend(
                    await self._verification_probe.resolve_dns_txt(base_url=managed_site.base_url)
                )
        else:
            if self._verification_probe is not None:
                proofs.append(
                    await self._verification_probe.fetch_script_proof(
                        base_url=managed_site.base_url
                    )
                )
        for proof in dict.fromkeys(proofs):
            if not 16 <= len(proof) <= 256 or any(character.isspace() for character in proof):
                continue
            site = await self._port.complete_verification(
                tenant_id=command.principal.tenant_id,
                site_id=site_id,
                method=method,
                token_hash=_verification_token_hash(proof, self._verification_token_secret),
                verified_at=datetime.now(UTC),
                actor_subject_id=command.principal.subject_id,
                correlation_id=command.correlation_id,
            )
            if site is not None:
                return site
        raise ValueError("site verification failed or challenge expired")

    async def _get_managed_site(self, *, tenant_id: str, site_id: str) -> ManagedSupportSite | None:
        # Keep older in-memory adapters usable while they adopt the point lookup port method.
        getter = getattr(self._port, "get_managed_site", None)
        if getter is not None:
            return await getter(tenant_id=tenant_id, site_id=site_id)
        sites = await self._port.list_managed_sites(tenant_id=tenant_id)
        return next((item for item in sites if item.site_id == site_id), None)


def hash_site_key(site_key: str) -> str:
    return sha256(site_key.encode("utf-8")).hexdigest()


def _site_key_projection(site_key: str) -> tuple[str, str]:
    if not 32 <= len(site_key) <= 256 or any(character.isspace() for character in site_key):
        raise ValueError("site key must contain 32 to 256 non-whitespace characters")
    return hash_site_key(site_key), site_key[:8]


def _validate_site_id(site_id: str) -> str:
    normalized = site_id.strip().casefold()
    if not _SITE_ID_PATTERN.fullmatch(normalized):
        raise ValueError("site_id must be a lowercase DNS-style identifier")
    return normalized


def _validate_name(name: str) -> str:
    normalized = name.strip()
    if not 1 <= len(normalized) <= 200:
        raise ValueError("site name must contain between 1 and 200 characters")
    return normalized


def _validate_base_url(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    if not normalized:
        return ""
    parsed = urlparse(normalized)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("base_url must be an absolute HTTPS origin")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("base_url must be an HTTPS origin without a path, query, or fragment")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("base_url contains an invalid port") from exc
    if port is not None and port != 443:
        raise ValueError("base_url must use the default HTTPS port")
    hostname = parsed.hostname or ""
    if not hostname or any(ord(character) > 127 for character in hostname):
        raise ValueError("base_url hostname must use ASCII DNS labels")
    if hostname.casefold() in {"localhost", "localhost.localdomain"}:
        raise ValueError("base_url must not target a local hostname")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    ):
        raise ValueError("base_url must not target a private or reserved address")
    if len(normalized) > 500:
        raise ValueError("base_url must contain at most 500 characters")
    return normalized


def _allowed_origins(base_url: str) -> tuple[str, ...]:
    parsed = urlparse(base_url)
    hostname = (parsed.hostname or "").casefold()
    port = parsed.port
    authority = hostname if port in {None, 443} else f"{hostname}:{port}"
    return (f"https://{authority}",)


def _verification_token_hash(token: str, secret: bytes) -> str:
    return hmac.new(secret, token.encode("utf-8"), hashlib.sha256).hexdigest()


def _validate_primary_language(language: str) -> str:
    normalized = language.strip().replace("_", "-").casefold()
    if not _LANGUAGE_PATTERN.fullmatch(normalized):
        raise ValueError("primary_language must be a valid language tag")
    return normalized


def _require_manage_sites(scopes: frozenset[str]) -> None:
    if "sites:manage" not in scopes:
        raise PermissionError("site management permission is required")
