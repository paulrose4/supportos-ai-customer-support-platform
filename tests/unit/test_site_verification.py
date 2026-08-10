from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.application.dto import (
    CompleteSiteVerificationCommand,
    CreateManagedSiteCommand,
    IssueSiteVerificationChallengeCommand,
)
from app.application.services.site_admin import SiteAdministrationService
from app.domain.models import AuthenticatedPrincipal, ManagedSupportSite
from app.domain.rules.rbac import scopes_for_roles


def _principal() -> AuthenticatedPrincipal:
    roles = frozenset({"tenant_owner"})
    return AuthenticatedPrincipal(
        subject_id="owner-1",
        tenant_id="tenant-a",
        roles=roles,
        scopes=scopes_for_roles(roles),
        authentication_method="admin_session",
        authenticated_at=datetime.now(UTC),
        correlation_id="verification-test",
    )


class VerificationPort:
    def __init__(self) -> None:
        now = datetime.now(UTC)
        self.site = ManagedSupportSite(
            site_id="shop",
            tenant_id="tenant-a",
            public_widget_id="site_pub_test_1234567890123456",
            name="Shop",
            base_url="https://shop.example.com",
            allowed_origins=("https://shop.example.com",),
            widget_daily_message_limit=500,
            status="active",
            credential_key_prefix=None,
            credential_status=None,
            created_at=now,
            updated_at=now,
            primary_language="en",
            verification_status="pending",
        )
        self.token_hash = ""
        self.expires_at: datetime | None = None
        self.list_calls = 0
        self.get_calls = 0

    async def list_managed_sites(self, *, tenant_id: str) -> list[ManagedSupportSite]:
        self.list_calls += 1
        return [self.site] if tenant_id == self.site.tenant_id else []

    async def get_managed_site(self, *, tenant_id: str, site_id: str) -> ManagedSupportSite | None:
        self.get_calls += 1
        if tenant_id == self.site.tenant_id and site_id == self.site.site_id:
            return self.site
        return None

    async def create_managed_site(self, **kwargs):  # type: ignore[no-untyped-def]
        return self.site

    async def update_managed_site(self, **kwargs):  # type: ignore[no-untyped-def]
        return self.site

    async def rotate_site_key(self, **kwargs):  # type: ignore[no-untyped-def]
        return self.site

    async def issue_verification_challenge(self, **kwargs):  # type: ignore[no-untyped-def]
        self.token_hash = kwargs["token_hash"]
        self.expires_at = kwargs["expires_at"]
        self.site = replace(
            self.site,
            verification_status="pending",
            verification_method=kwargs["method"],
            verification_token_prefix=kwargs["token_prefix"],
            verification_expires_at=kwargs["expires_at"],
        )
        return self.site

    async def complete_verification(self, **kwargs):  # type: ignore[no-untyped-def]
        if kwargs["token_hash"] != self.token_hash or self.expires_at is None:
            return None
        if self.expires_at <= kwargs["verified_at"]:
            return None
        self.site = replace(
            self.site,
            verification_status="verified",
            verified_at=kwargs["verified_at"],
            verification_token_prefix=None,
            verification_expires_at=None,
        )
        return self.site


class VerificationProbe:
    def __init__(self) -> None:
        self.proof = ""

    async def resolve_dns_txt(self, *, base_url: str) -> list[str]:
        del base_url
        return []

    async def fetch_script_proof(self, *, base_url: str) -> str:
        del base_url
        return self.proof


async def test_site_verification_requires_an_external_proof() -> None:
    port = VerificationPort()
    probe = VerificationProbe()
    service = SiteAdministrationService(
        port,
        verification_token_secret="test-secret",
        verification_probe=probe,
    )
    challenge = await service.issue_verification_challenge(
        IssueSiteVerificationChallengeCommand(_principal(), "shop", "script", "challenge")
    )
    probe.proof = challenge.script_value

    verified = await service.verify_site(
        CompleteSiteVerificationCommand(_principal(), "shop", "script", "verify")
    )

    assert verified.verification_status == "verified"
    assert verified.verified_at is not None
    assert port.get_calls == 2
    assert port.list_calls == 0


async def test_site_origin_rejects_private_addresses() -> None:
    service = SiteAdministrationService(VerificationPort())
    with pytest.raises(ValueError, match="private or reserved"):
        await service.create_site(
            CreateManagedSiteCommand(
                _principal(), "private", "Private", "https://127.0.0.1", None, "create"
            )
        )
