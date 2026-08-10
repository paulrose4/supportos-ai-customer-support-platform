import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from app.application.dto.widget import (
    AuthenticatePublicPresenceCommand,
    AuthenticatePublicPresenceResult,
    AuthenticatePublicWidgetCommand,
    AuthenticatePublicWidgetResult,
    BootstrapPublicWidgetCommand,
    BootstrapPublicWidgetResult,
    GetPublicWidgetAppearanceQuery,
    PublicWidgetAppearanceResult,
)
from app.application.services.customer_experience import default_widget_config
from app.domain.models import AuthenticatedPrincipal
from app.domain.models.widget import WidgetCapacityLease
from app.domain.ports.metrics import RequestMetricsPort
from app.domain.ports.widget import (
    PublicWidgetAccessPort,
    PublicWidgetSessionPort,
    PublicWidgetTokenPort,
    WidgetCapacityPort,
    WidgetRateLimitPort,
)

_PUBLIC_WIDGET_ID_PATTERN = re.compile(r"^site_pub_[A-Za-z0-9_-]{16,64}$")
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,100}$")


class PublicWidgetAccessDeniedError(PermissionError):
    pass


class PublicWidgetRateLimitError(PermissionError):
    pass


class PublicWidgetQuotaExceededError(PermissionError):
    pass


class PublicWidgetSessionService:
    def __init__(
        self,
        *,
        access: PublicWidgetAccessPort,
        tokens: PublicWidgetTokenPort,
        rate_limits: WidgetRateLimitPort,
        bootstrap_limit_per_minute: int,
        chat_limit_per_minute: int,
        presence_limit_per_minute: int,
        presence_source_limit_per_minute: int = 120,
        presence_site_limit_per_minute: int = 30_000,
        sessions: PublicWidgetSessionPort | None = None,
        resume_ttl_seconds: int = 2_592_000,
        capacity: WidgetCapacityPort | None = None,
        metrics: RequestMetricsPort | None = None,
    ) -> None:
        self._access = access
        self._tokens = tokens
        self._rate_limits = rate_limits
        self._bootstrap_limit = bootstrap_limit_per_minute
        self._chat_limit = chat_limit_per_minute
        self._presence_limit = presence_limit_per_minute
        self._presence_source_limit = presence_source_limit_per_minute
        self._presence_site_limit = presence_site_limit_per_minute
        self._sessions = sessions
        self._resume_ttl = timedelta(seconds=resume_ttl_seconds)
        self._capacity = capacity
        self._metrics = metrics

    async def appearance(
        self, query: GetPublicWidgetAppearanceQuery
    ) -> PublicWidgetAppearanceResult:
        now = datetime.now(UTC)
        public_widget_id = query.public_widget_id.strip()
        origin = _normalize_origin(query.origin)
        if not _PUBLIC_WIDGET_ID_PATTERN.fullmatch(public_widget_id) or origin is None:
            raise PublicWidgetAccessDeniedError("public widget access is denied")
        allowed = await self._rate_limits.consume(
            bucket="widget-appearance",
            source_key=query.source_address,
            limit=self._bootstrap_limit,
            window_seconds=60,
            occurred_at=now,
        )
        if not allowed:
            raise PublicWidgetRateLimitError("public widget appearance rate limit exceeded")
        site = await self._access.get_public_site(public_widget_id=public_widget_id)
        if (
            site is None
            or site.status != "active"
            or site.verification_status != "verified"
            or origin not in site.allowed_origins
        ):
            raise PublicWidgetAccessDeniedError("public widget access is denied")
        config = site.widget_config or default_widget_config(site.primary_language)
        return PublicWidgetAppearanceResult(
            widget_config=config,
            widget_config_version=site.widget_config_version,
            is_online=_within_business_hours(
                config.business_timezone,
                config.business_hours,
                config.holidays,
                now,
            ),
        )

    async def bootstrap(self, command: BootstrapPublicWidgetCommand) -> BootstrapPublicWidgetResult:
        now = datetime.now(UTC)
        public_widget_id = command.public_widget_id.strip()
        origin = _normalize_origin(command.origin)
        if not _PUBLIC_WIDGET_ID_PATTERN.fullmatch(public_widget_id) or origin is None:
            raise PublicWidgetAccessDeniedError("public widget access is denied")
        allowed = await self._rate_limits.consume(
            bucket="widget-bootstrap",
            source_key=command.source_address,
            limit=self._bootstrap_limit,
            window_seconds=60,
            occurred_at=now,
        )
        if not allowed:
            raise PublicWidgetRateLimitError("public widget bootstrap rate limit exceeded")
        site = await self._access.get_public_site(public_widget_id=public_widget_id)
        if (
            site is None
            or site.status != "active"
            or site.verification_status != "verified"
            or origin not in site.allowed_origins
        ):
            raise PublicWidgetAccessDeniedError("public widget access is denied")
        session_id = None
        issued_session = None
        if command.resume_token:
            if self._sessions is None:
                raise PublicWidgetAccessDeniedError("public widget resume is unavailable")
            issued_session = await self._sessions.rotate_visitor_session(
                site=site,
                origin=origin,
                resume_token=command.resume_token,
                occurred_at=now,
                expires_at=now + self._resume_ttl,
            )
            if issued_session is None:
                raise PublicWidgetAccessDeniedError("public widget resume is invalid")
            session_id = issued_session.session_id
        elif command.session_token:
            try:
                previous_claims = self._tokens.verify(token=command.session_token, verified_at=now)
            except PermissionError as exc:
                raise PublicWidgetAccessDeniedError("public widget session is invalid") from exc
            if (
                previous_claims.public_widget_id != site.public_widget_id
                or previous_claims.origin != origin
                or (
                    previous_claims.token_version == 2
                    and previous_claims.auth_version != site.auth_version
                )
            ):
                raise PublicWidgetAccessDeniedError("public widget session is invalid")
            session_id = previous_claims.visitor_session_id
            if (
                previous_claims.token_version == 2
                and self._sessions is not None
                and not await self._sessions.visitor_session_is_active(
                    tenant_id=site.tenant_id,
                    site_id=site.site_id,
                    session_id=session_id,
                    occurred_at=now,
                )
            ):
                raise PublicWidgetAccessDeniedError("public widget session is inactive")
            if previous_claims.token_version == 1 and self._sessions is not None:
                issued_session = await self._sessions.create_visitor_session(
                    site=site,
                    origin=origin,
                    occurred_at=now,
                    expires_at=now + self._resume_ttl,
                    preferred_session_id=session_id,
                )
        elif self._sessions is not None:
            issued_session = await self._sessions.create_visitor_session(
                site=site,
                origin=origin,
                occurred_at=now,
                expires_at=now + self._resume_ttl,
            )
            session_id = issued_session.session_id
        issued = self._tokens.issue(
            site=site,
            origin=origin,
            issued_at=now,
            session_id=session_id,
        )
        config = site.widget_config or default_widget_config(site.primary_language)
        return BootstrapPublicWidgetResult(
            issued.token,
            issued.expires_at,
            site.primary_language,
            config,
            _within_business_hours(
                config.business_timezone,
                config.business_hours,
                config.holidays,
                now,
            ),
            issued_session.resume_token if issued_session else None,
            issued_session.expires_at if issued_session else None,
            site.widget_config_version,
        )

    async def authenticate(
        self, command: AuthenticatePublicWidgetCommand
    ) -> AuthenticatePublicWidgetResult:
        now = datetime.now(UTC)
        origin = _normalize_origin(command.origin)
        if origin is None:
            raise PublicWidgetAccessDeniedError("public widget access is denied")
        try:
            claims = self._tokens.verify(token=command.session_token, verified_at=now)
        except PermissionError as exc:
            raise PublicWidgetAccessDeniedError("public widget access is denied") from exc
        site = await self._access.get_public_site(public_widget_id=claims.public_widget_id)
        if (
            site is None
            or site.status != "active"
            or site.verification_status != "verified"
            or origin != claims.origin
            or origin not in site.allowed_origins
            or (claims.token_version == 2 and claims.auth_version != site.auth_version)
        ):
            raise PublicWidgetAccessDeniedError("public widget access is denied")
        limit = self._operation_limit(command.operation)
        allowed = await self._rate_limits.consume(
            bucket=f"widget-{command.operation}",
            source_key=f"{site.public_widget_id}:{command.source_address}",
            limit=limit,
            window_seconds=60,
            occurred_at=now,
        )
        if not allowed:
            raise PublicWidgetRateLimitError("public widget request rate limit exceeded")
        config = site.widget_config or default_widget_config(site.primary_language)
        principal = AuthenticatedPrincipal(
            subject_id="anonymous-widget-visitor",
            tenant_id=site.tenant_id,
            roles=frozenset({"anonymous"}),
            scopes=frozenset({"knowledge:read"}),
            authentication_method="public_widget_token",
            authenticated_at=now,
            correlation_id=command.correlation_id,
            site_id=site.site_id,
            preferred_language=site.primary_language,
            site_domain=site.base_url or origin,
            agent_display_name=config.agent_name,
            agent_identity_type="team",
            customer_address_mode=config.customer_address_mode,
            introduce_on_first_turn=config.introduce_on_first_turn,
            site_identity_version=site.widget_config_version,
            visitor_session_id=claims.visitor_session_id,
        )
        return AuthenticatePublicWidgetResult(principal)

    async def authenticate_presence(
        self,
        command: AuthenticatePublicPresenceCommand,
    ) -> AuthenticatePublicPresenceResult:
        now = datetime.now(UTC)
        origin = _normalize_origin(command.origin)
        visitor_id = command.visitor_id.strip()
        if origin is None or not _REQUEST_ID_PATTERN.fullmatch(visitor_id):
            raise PublicWidgetAccessDeniedError("public widget presence access is denied")
        if bool(command.public_widget_id) == bool(command.presence_token):
            raise PublicWidgetAccessDeniedError("exactly one presence credential is required")

        if command.presence_token:
            try:
                claims = self._tokens.verify_presence(
                    token=command.presence_token,
                    visitor_id=visitor_id,
                    verified_at=now,
                )
            except PermissionError as exc:
                raise PublicWidgetAccessDeniedError(
                    "public widget presence access is denied"
                ) from exc
            public_widget_id = claims.public_widget_id
        else:
            public_widget_id = (command.public_widget_id or "").strip()
            if not _PUBLIC_WIDGET_ID_PATTERN.fullmatch(public_widget_id):
                raise PublicWidgetAccessDeniedError("public widget presence access is denied")
            claims = None

        if not await self._rate_limits.consume(
            bucket="widget-presence-source",
            source_key=command.source_address,
            limit=self._presence_source_limit * 2,
            window_seconds=60,
            occurred_at=now,
        ):
            raise PublicWidgetRateLimitError("public widget presence rate limit exceeded")
        site = await self._access.get_public_site(public_widget_id=public_widget_id)
        if (
            site is None
            or site.status != "active"
            or site.verification_status != "verified"
            or origin not in site.allowed_origins
            or (
                claims is not None
                and (claims.origin != origin or claims.auth_version != site.auth_version)
            )
        ):
            raise PublicWidgetAccessDeniedError("public widget presence access is denied")

        rate_limit_checks = (
            (
                "widget-presence-visitor",
                f"{site.public_widget_id}:{visitor_id}",
                self._presence_limit,
            ),
            (
                "widget-presence-site-source",
                f"{site.public_widget_id}:{command.source_address}",
                self._presence_source_limit,
            ),
            (
                "widget-presence-site",
                site.public_widget_id,
                self._presence_site_limit,
            ),
        )
        for bucket, source_key, limit in rate_limit_checks:
            if not await self._rate_limits.consume(
                bucket=bucket,
                source_key=source_key,
                limit=limit,
                window_seconds=60,
                occurred_at=now,
            ):
                raise PublicWidgetRateLimitError("public widget presence rate limit exceeded")

        if claims is None:
            issued = self._tokens.issue_presence(
                site=site,
                origin=origin,
                visitor_id=visitor_id,
                issued_at=now,
            )
            presence_token = issued.token
            expires_at = issued.expires_at
        else:
            presence_token = command.presence_token or ""
            expires_at = claims.expires_at
        config = site.widget_config or default_widget_config(site.primary_language)
        principal = AuthenticatedPrincipal(
            subject_id="anonymous-presence-visitor",
            tenant_id=site.tenant_id,
            roles=frozenset({"anonymous"}),
            scopes=frozenset(),
            authentication_method="public_presence_token",
            authenticated_at=now,
            correlation_id=command.correlation_id,
            site_id=site.site_id,
            preferred_language=site.primary_language,
            site_domain=site.base_url or origin,
            agent_display_name=config.agent_name,
            site_identity_version=site.widget_config_version,
        )
        return AuthenticatePublicPresenceResult(
            principal=principal,
            presence_token=presence_token,
            expires_at=expires_at,
        )

    async def admit_chat(
        self,
        *,
        principal: AuthenticatedPrincipal,
        request_id: str,
    ) -> None:
        normalized_request_id = request_id.strip()
        if not _REQUEST_ID_PATTERN.fullmatch(normalized_request_id):
            raise ValueError("request_id must be an opaque identifier")
        if not principal.site_id:
            raise PublicWidgetAccessDeniedError("public widget site identity is unavailable")
        admitted = await self._access.admit_message(
            tenant_id=principal.tenant_id,
            site_id=principal.site_id,
            request_id=normalized_request_id,
            occurred_at=datetime.now(UTC),
        )
        if not admitted:
            if self._metrics is not None:
                self._metrics.record_widget_admission(outcome="daily_quota")
            raise PublicWidgetQuotaExceededError("public widget daily message limit exceeded")
        if self._metrics is not None:
            self._metrics.record_widget_admission(outcome="admitted")

    async def acquire_chat_capacity(
        self, principal: AuthenticatedPrincipal
    ) -> WidgetCapacityLease | None:
        if not principal.site_id:
            raise PublicWidgetAccessDeniedError("public widget site identity is unavailable")
        if self._capacity is None:
            lease = WidgetCapacityLease("unbounded", principal.tenant_id, principal.site_id)
        else:
            if self._metrics is not None:
                self._metrics.adjust_widget_chat_queue(1)
            try:
                lease = await self._capacity.acquire(
                    tenant_id=principal.tenant_id,
                    site_id=principal.site_id,
                )
            finally:
                if self._metrics is not None:
                    self._metrics.adjust_widget_chat_queue(-1)
        if self._metrics is not None:
            if lease is None:
                self._metrics.record_widget_admission(outcome="capacity")
            else:
                self._metrics.adjust_widget_chat_in_flight(1)
        return lease

    async def release_chat_capacity(self, lease: WidgetCapacityLease) -> None:
        if self._capacity is not None and lease.member != "unbounded":
            await self._capacity.release(lease)
        if self._metrics is not None:
            self._metrics.adjust_widget_chat_in_flight(-1)

    def _operation_limit(self, operation: str) -> int:
        if operation == "chat":
            return self._chat_limit
        if operation == "presence":
            return self._presence_limit
        if operation in {"messages", "conversation-state", "events"}:
            return self._presence_limit
        if operation in {"satisfaction", "offline-message"}:
            return self._chat_limit
        raise ValueError("unsupported public widget operation")


def _within_business_hours(
    timezone_name: str,
    business_hours: dict[str, str],
    holidays: tuple[str, ...],
    occurred_at: datetime,
) -> bool:
    local = occurred_at.astimezone(ZoneInfo(timezone_name))
    if local.date().isoformat() in holidays:
        return False
    weekday = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[local.weekday()]
    hours = business_hours.get(weekday)
    if hours is None:
        return False
    start, end = hours.split("-", 1)
    return start <= local.strftime("%H:%M") < end


def _normalize_origin(value: str) -> str | None:
    parsed = urlparse(value.strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return None
    return f"{parsed.scheme.casefold()}://{parsed.netloc.casefold()}"
