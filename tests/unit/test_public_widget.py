from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.application.dto import (
    AuthenticatePublicPresenceCommand,
    AuthenticatePublicWidgetCommand,
    BootstrapPublicWidgetCommand,
    RecordVisitorPresenceCommand,
)
from app.application.dto.widget import GetPublicWidgetAppearanceQuery
from app.application.services import (
    PublicWidgetAccessDeniedError,
    PublicWidgetQuotaExceededError,
    PublicWidgetRateLimitError,
    PublicWidgetSessionService,
    VisitorPresenceService,
)
from app.domain.models import PublicWidgetSite
from app.domain.models.widget import IssuedPublicWidgetSession
from app.integrations.auth import HmacPublicWidgetCursorAdapter, HmacPublicWidgetTokenAdapter
from app.integrations.presence import InMemoryVisitorPresenceStore


class InMemoryPublicWidgetAccess:
    def __init__(self, site: PublicWidgetSite) -> None:
        self.site = site
        self.admissions: set[str] = set()
        self.volume_anomaly_detected = False

    async def get_public_site(self, *, public_widget_id: str):  # type: ignore[no-untyped-def]
        return self.site if public_widget_id == self.site.public_widget_id else None

    async def admit_message(
        self,
        *,
        tenant_id: str,
        site_id: str,
        request_id: str,
        occurred_at: datetime,
    ) -> bool:
        del occurred_at
        assert tenant_id == self.site.tenant_id
        assert site_id == self.site.site_id
        if request_id in self.admissions:
            return True
        if len(self.admissions) >= self.site.daily_message_limit:
            self.volume_anomaly_detected = True
            return False
        self.admissions.add(request_id)
        return True


class InMemoryRateLimits:
    def __init__(self) -> None:
        self.denied_buckets: set[str] = set()

    async def consume(self, *, bucket: str, **_kwargs) -> bool:  # type: ignore[no-untyped-def]
        return bucket not in self.denied_buckets


class InMemoryWidgetSessions:
    def __init__(self) -> None:
        self._sequence = 0
        self._by_resume_token: dict[str, tuple[str, str, str]] = {}
        self.active_sessions: set[tuple[str, str, str]] = set()

    async def create_visitor_session(
        self,
        *,
        site: PublicWidgetSite,
        origin: str,
        occurred_at: datetime,
        expires_at: datetime,
        preferred_session_id: str | None = None,
    ) -> IssuedPublicWidgetSession:
        del occurred_at
        self._sequence += 1
        session_id = preferred_session_id or f"session-{self._sequence}"
        resume_token = f"resume-token-{self._sequence}-" + "x" * 32
        self._by_resume_token[resume_token] = (session_id, site.site_id, origin)
        self.active_sessions.add((site.tenant_id, site.site_id, session_id))
        return IssuedPublicWidgetSession(session_id, resume_token, expires_at)

    async def rotate_visitor_session(
        self,
        *,
        site: PublicWidgetSite,
        origin: str,
        resume_token: str,
        occurred_at: datetime,
        expires_at: datetime,
    ) -> IssuedPublicWidgetSession | None:
        del occurred_at
        record = self._by_resume_token.pop(resume_token, None)
        if record is None or record[1:] != (site.site_id, origin):
            return None
        self._sequence += 1
        next_token = f"resume-token-{self._sequence}-" + "x" * 32
        self._by_resume_token[next_token] = record
        return IssuedPublicWidgetSession(record[0], next_token, expires_at)

    async def visitor_session_is_active(
        self,
        *,
        tenant_id: str,
        site_id: str,
        session_id: str,
        occurred_at: datetime,
    ) -> bool:
        del occurred_at
        return (tenant_id, site_id, session_id) in self.active_sessions


def site(*, limit: int = 2, primary_language: str = "en") -> PublicWidgetSite:
    return PublicWidgetSite(
        public_widget_id="site_pub_abcdefghijklmnop",
        tenant_id="tenant-a",
        site_id="site-a",
        allowed_origins=("https://shop.example.com",),
        status="active",
        daily_message_limit=limit,
        primary_language=primary_language,
    )


def service(*, limit: int = 2):  # type: ignore[no-untyped-def]
    access = InMemoryPublicWidgetAccess(site(limit=limit))
    rate_limits = InMemoryRateLimits()
    sessions = PublicWidgetSessionService(
        access=access,
        tokens=HmacPublicWidgetTokenAdapter(secret="s" * 32, ttl_seconds=900),
        rate_limits=rate_limits,
        bootstrap_limit_per_minute=30,
        chat_limit_per_minute=20,
        presence_limit_per_minute=12,
    )
    return sessions, access, rate_limits


async def bootstrap(sessions: PublicWidgetSessionService):  # type: ignore[no-untyped-def]
    return await sessions.bootstrap(
        BootstrapPublicWidgetCommand(
            public_widget_id="site_pub_abcdefghijklmnop",
            origin="https://shop.example.com",
            source_address="203.0.113.5",
            correlation_id="bootstrap-1",
        )
    )


async def test_public_widget_bootstrap_maps_trusted_origin_to_short_lived_token() -> None:
    sessions, _, _ = service()

    issued = await bootstrap(sessions)
    authenticated = await sessions.authenticate(
        AuthenticatePublicWidgetCommand(
            session_token=issued.session_token,
            origin="https://shop.example.com",
            source_address="203.0.113.5",
            correlation_id="chat-1",
            operation="chat",
            request_id="request-1",
        )
    )

    assert authenticated.principal.tenant_id == "tenant-a"
    assert authenticated.principal.site_id == "site-a"
    assert authenticated.principal.authentication_method == "public_widget_token"
    assert authenticated.principal.scopes == frozenset({"knowledge:read"})
    assert issued.primary_language == "en"
    assert authenticated.principal.preferred_language == "en"


async def test_public_presence_token_is_visitor_bound_and_creates_no_chat_session() -> None:
    access = InMemoryPublicWidgetAccess(site())
    rate_limits = InMemoryRateLimits()
    visitor_sessions = InMemoryWidgetSessions()
    tokens = HmacPublicWidgetTokenAdapter(secret="s" * 32, ttl_seconds=900)
    sessions = PublicWidgetSessionService(
        access=access,
        tokens=tokens,
        rate_limits=rate_limits,
        bootstrap_limit_per_minute=30,
        chat_limit_per_minute=20,
        presence_limit_per_minute=6,
        sessions=visitor_sessions,
    )

    first = await sessions.authenticate_presence(
        AuthenticatePublicPresenceCommand(
            visitor_id="visitor-1",
            public_widget_id="site_pub_abcdefghijklmnop",
            presence_token=None,
            origin="https://shop.example.com",
            source_address="203.0.113.5",
            correlation_id="presence-1",
        )
    )
    second = await sessions.authenticate_presence(
        AuthenticatePublicPresenceCommand(
            visitor_id="visitor-1",
            public_widget_id=None,
            presence_token=first.presence_token,
            origin="https://shop.example.com",
            source_address="203.0.113.5",
            correlation_id="presence-2",
        )
    )

    assert first.principal.tenant_id == "tenant-a"
    assert first.principal.site_id == "site-a"
    assert first.principal.authentication_method == "public_presence_token"
    assert second.presence_token == first.presence_token
    assert visitor_sessions.active_sessions == set()
    recorded = await VisitorPresenceService(InMemoryVisitorPresenceStore()).record(
        RecordVisitorPresenceCommand(
            principal=first.principal,
            visitor_id="visitor-1",
            conversation_id=None,
            page_path="/products",
            page_view_id="page-1",
            presence_source="page_load",
        )
    )
    assert recorded.site_id == "site-a"
    with pytest.raises(PermissionError):
        tokens.verify(token=first.presence_token, verified_at=datetime.now(UTC))
    with pytest.raises(PublicWidgetAccessDeniedError):
        await sessions.authenticate_presence(
            AuthenticatePublicPresenceCommand(
                visitor_id="visitor-2",
                public_widget_id=None,
                presence_token=first.presence_token,
                origin="https://shop.example.com",
                source_address="203.0.113.5",
                correlation_id="presence-mismatch",
            )
        )


async def test_public_widget_rejects_unregistered_origin_and_rate_limit() -> None:
    sessions, _, rate_limits = service()

    with pytest.raises(PublicWidgetAccessDeniedError):
        await sessions.bootstrap(
            BootstrapPublicWidgetCommand(
                "site_pub_abcdefghijklmnop",
                "https://evil.example",
                "203.0.113.5",
                "bootstrap-1",
            )
        )

    rate_limits.denied_buckets.add("widget-bootstrap")
    with pytest.raises(PublicWidgetRateLimitError):
        await bootstrap(sessions)


async def test_public_widget_appearance_uses_published_site_config_and_origin_rules() -> None:
    sessions, access, rate_limits = service()
    access.site = replace(access.site, widget_config_version="widget-version-7")

    appearance = await sessions.appearance(
        GetPublicWidgetAppearanceQuery(
            public_widget_id=access.site.public_widget_id,
            origin="https://shop.example.com",
            source_address="203.0.113.5",
            correlation_id="appearance-1",
        )
    )

    assert appearance.widget_config_version == "widget-version-7"
    assert isinstance(appearance.is_online, bool)
    with pytest.raises(PublicWidgetAccessDeniedError):
        await sessions.appearance(
            GetPublicWidgetAppearanceQuery(
                public_widget_id=access.site.public_widget_id,
                origin="https://evil.example",
                source_address="203.0.113.5",
                correlation_id="appearance-2",
            )
        )
    rate_limits.denied_buckets.add("widget-appearance")
    with pytest.raises(PublicWidgetRateLimitError):
        await sessions.appearance(
            GetPublicWidgetAppearanceQuery(
                public_widget_id=access.site.public_widget_id,
                origin="https://shop.example.com",
                source_address="203.0.113.5",
                correlation_id="appearance-3",
            )
        )


async def test_public_widget_daily_quota_is_hard_and_idempotent() -> None:
    sessions, access, _ = service(limit=1)
    issued = await bootstrap(sessions)
    command = AuthenticatePublicWidgetCommand(
        issued.session_token,
        "https://shop.example.com",
        "203.0.113.5",
        "chat-1",
        "chat",
        "request-1",
    )

    authenticated = await sessions.authenticate(command)
    await sessions.admit_chat(principal=authenticated.principal, request_id="request-1")
    await sessions.admit_chat(principal=authenticated.principal, request_id="request-1")
    assert access.admissions == {"request-1"}

    with pytest.raises(PublicWidgetQuotaExceededError):
        await sessions.admit_chat(principal=authenticated.principal, request_id="request-2")
    assert access.admissions == {"request-1"}
    assert access.volume_anomaly_detected is True


def test_widget_token_rejects_tampering_and_expiry() -> None:
    tokens = HmacPublicWidgetTokenAdapter(secret="s" * 32, ttl_seconds=60)
    now = datetime.now(UTC)
    issued = tokens.issue(site=site(), origin="https://shop.example.com", issued_at=now)

    with pytest.raises(PermissionError):
        tokens.verify(token=issued.token + "tampered", verified_at=now)
    with pytest.raises(PermissionError):
        tokens.verify(token=issued.token, verified_at=now + timedelta(seconds=61))


def test_widget_token_v2_rotates_jti_without_changing_stable_session() -> None:
    tokens = HmacPublicWidgetTokenAdapter(secret="s" * 32, ttl_seconds=60)
    now = datetime.now(UTC)
    first = tokens.issue(
        site=site(),
        origin="https://shop.example.com",
        issued_at=now,
        session_id="stable-session",
    )
    second = tokens.issue(
        site=site(),
        origin="https://shop.example.com",
        issued_at=now,
        session_id="stable-session",
    )

    first_claims = tokens.verify(token=first.token, verified_at=now)
    second_claims = tokens.verify(token=second.token, verified_at=now)

    assert first_claims.token_id != second_claims.token_id
    assert first_claims.visitor_session_id == "stable-session"
    assert second_claims.visitor_session_id == "stable-session"


def test_widget_message_cursor_is_bound_to_session_and_conversation() -> None:
    cursors = HmacPublicWidgetCursorAdapter(secret="s" * 32)
    cursor = cursors.issue(
        tenant_id="tenant-a",
        site_id="site-a",
        session_id="session-a",
        conversation_id="conversation-a",
        after_id=42,
    )

    assert (
        cursors.verify(
            cursor=cursor,
            tenant_id="tenant-a",
            site_id="site-a",
            session_id="session-a",
            conversation_id="conversation-a",
        )
        == 42
    )
    with pytest.raises(PermissionError):
        cursors.verify(
            cursor=cursor,
            tenant_id="tenant-a",
            site_id="site-a",
            session_id="session-b",
            conversation_id="conversation-a",
        )
    with pytest.raises(PermissionError):
        cursors.verify(
            cursor=cursor[:-1] + ("A" if cursor[-1] != "A" else "B"),
            tenant_id="tenant-a",
            site_id="site-a",
            session_id="session-a",
            conversation_id="conversation-a",
        )


async def test_resume_token_rotates_without_changing_sid_and_revocation_blocks_access() -> None:
    access = InMemoryPublicWidgetAccess(site())
    visitor_sessions = InMemoryWidgetSessions()
    sessions = PublicWidgetSessionService(
        access=access,
        tokens=HmacPublicWidgetTokenAdapter(secret="s" * 32, ttl_seconds=900),
        rate_limits=InMemoryRateLimits(),
        bootstrap_limit_per_minute=30,
        chat_limit_per_minute=20,
        presence_limit_per_minute=12,
        sessions=visitor_sessions,
    )
    first = await bootstrap(sessions)
    assert first.resume_token is not None

    second = await sessions.bootstrap(
        BootstrapPublicWidgetCommand(
            public_widget_id="site_pub_abcdefghijklmnop",
            origin="https://shop.example.com",
            source_address="203.0.113.5",
            correlation_id="bootstrap-2",
            resume_token=first.resume_token,
        )
    )
    tokens = HmacPublicWidgetTokenAdapter(secret="s" * 32, ttl_seconds=900)
    first_claims = tokens.verify(token=first.session_token, verified_at=datetime.now(UTC))
    second_claims = tokens.verify(token=second.session_token, verified_at=datetime.now(UTC))

    assert second.resume_token is not None
    assert second.resume_token != first.resume_token
    assert first_claims.visitor_session_id == second_claims.visitor_session_id

    visitor_sessions.active_sessions.clear()
    with pytest.raises(PublicWidgetAccessDeniedError, match="inactive"):
        await sessions.bootstrap(
            BootstrapPublicWidgetCommand(
                public_widget_id="site_pub_abcdefghijklmnop",
                origin="https://shop.example.com",
                source_address="203.0.113.5",
                correlation_id="refresh-revoked",
                session_token=second.session_token,
            )
        )
