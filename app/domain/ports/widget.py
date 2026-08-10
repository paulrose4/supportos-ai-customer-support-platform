from datetime import datetime
from typing import Protocol

from app.domain.models.widget import (
    IssuedPublicWidgetSession,
    IssuedPublicWidgetToken,
    PublicPresenceTokenClaims,
    PublicWidgetSite,
    PublicWidgetTokenClaims,
    WidgetCapacityLease,
)


class PublicWidgetAccessPort(Protocol):
    async def get_public_site(self, *, public_widget_id: str) -> PublicWidgetSite | None: ...

    async def admit_message(
        self,
        *,
        tenant_id: str,
        site_id: str,
        request_id: str,
        occurred_at: datetime,
    ) -> bool: ...


class PublishedWidgetCachePort(Protocol):
    async def invalidate(self, *, public_widget_id: str) -> None: ...


class PublicWidgetTokenPort(Protocol):
    def issue(
        self,
        *,
        site: PublicWidgetSite,
        origin: str,
        issued_at: datetime,
        session_id: str | None = None,
    ) -> IssuedPublicWidgetToken: ...

    def verify(self, *, token: str, verified_at: datetime) -> PublicWidgetTokenClaims: ...

    def issue_presence(
        self,
        *,
        site: PublicWidgetSite,
        origin: str,
        visitor_id: str,
        issued_at: datetime,
    ) -> IssuedPublicWidgetToken: ...

    def verify_presence(
        self,
        *,
        token: str,
        visitor_id: str,
        verified_at: datetime,
    ) -> PublicPresenceTokenClaims: ...


class PublicWidgetSessionPort(Protocol):
    async def create_visitor_session(
        self,
        *,
        site: PublicWidgetSite,
        origin: str,
        occurred_at: datetime,
        expires_at: datetime,
        preferred_session_id: str | None = None,
    ) -> IssuedPublicWidgetSession: ...

    async def rotate_visitor_session(
        self,
        *,
        site: PublicWidgetSite,
        origin: str,
        resume_token: str,
        occurred_at: datetime,
        expires_at: datetime,
    ) -> IssuedPublicWidgetSession | None: ...

    async def visitor_session_is_active(
        self,
        *,
        tenant_id: str,
        site_id: str,
        session_id: str,
        occurred_at: datetime,
    ) -> bool: ...


class PublicWidgetCursorPort(Protocol):
    def issue(
        self,
        *,
        tenant_id: str,
        site_id: str,
        session_id: str,
        conversation_id: str,
        after_id: int,
    ) -> str: ...

    def verify(
        self,
        *,
        cursor: str,
        tenant_id: str,
        site_id: str,
        session_id: str,
        conversation_id: str,
    ) -> int: ...


class WidgetCapacityPort(Protocol):
    async def acquire(
        self,
        *,
        tenant_id: str,
        site_id: str,
    ) -> WidgetCapacityLease | None: ...

    async def release(self, lease: WidgetCapacityLease) -> None: ...


class WidgetRateLimitPort(Protocol):
    async def consume(
        self,
        *,
        bucket: str,
        source_key: str,
        limit: int,
        window_seconds: int,
        occurred_at: datetime,
    ) -> bool: ...
