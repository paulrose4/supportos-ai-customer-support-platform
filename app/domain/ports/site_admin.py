from datetime import datetime
from typing import Protocol

from app.domain.models.site_admin import ManagedSupportSite


class SiteVerificationProbePort(Protocol):
    async def resolve_dns_txt(self, *, base_url: str) -> list[str]: ...

    async def fetch_script_proof(self, *, base_url: str) -> str: ...


class SiteAdministrationPort(Protocol):
    async def list_managed_sites(self, *, tenant_id: str) -> list[ManagedSupportSite]: ...

    async def get_managed_site(
        self, *, tenant_id: str, site_id: str
    ) -> ManagedSupportSite | None: ...

    async def create_managed_site(
        self,
        *,
        tenant_id: str,
        site_id: str,
        public_widget_id: str,
        name: str,
        base_url: str,
        allowed_origins: tuple[str, ...],
        widget_daily_message_limit: int,
        primary_language: str,
        key_hash: str | None,
        key_prefix: str | None,
        actor_subject_id: str,
        correlation_id: str,
        created_at: datetime,
    ) -> ManagedSupportSite | None: ...

    async def update_managed_site(
        self,
        *,
        tenant_id: str,
        site_id: str,
        name: str,
        base_url: str,
        allowed_origins: tuple[str, ...],
        status: str,
        primary_language: str,
        actor_subject_id: str,
        correlation_id: str,
        changed_at: datetime,
    ) -> ManagedSupportSite | None: ...

    async def rotate_site_key(
        self,
        *,
        tenant_id: str,
        site_id: str,
        key_hash: str,
        key_prefix: str,
        actor_subject_id: str,
        correlation_id: str,
        changed_at: datetime,
    ) -> ManagedSupportSite | None: ...

    async def issue_verification_challenge(
        self,
        *,
        tenant_id: str,
        site_id: str,
        method: str,
        token_hash: str,
        token_prefix: str,
        expires_at: datetime,
        changed_at: datetime,
        actor_subject_id: str,
        correlation_id: str,
    ) -> ManagedSupportSite | None: ...

    async def complete_verification(
        self,
        *,
        tenant_id: str,
        site_id: str,
        method: str,
        token_hash: str,
        verified_at: datetime,
        actor_subject_id: str,
        correlation_id: str,
    ) -> ManagedSupportSite | None: ...
