from datetime import UTC, datetime

import pytest

from app.application.dto.onboarding import IssueWorkspaceOnboardingCodeCommand
from app.application.services.self_service_onboarding import SelfServiceTenantProvisioningService
from app.domain.models import AuthenticatedPrincipal


class FakeOnboardingStore:
    def __init__(self) -> None:
        self.authorities = []
        self.policies = []
        self.codes = []

    async def create_authority(self, authority, *, correlation_id):  # type: ignore[no-untyped-def]
        del correlation_id
        self.authorities.append(authority)
        return authority

    async def create_policy(self, policy, *, correlation_id):  # type: ignore[no-untyped-def]
        del correlation_id
        self.policies.append(policy)
        return policy

    async def create_code(self, code, *, correlation_id):  # type: ignore[no-untyped-def]
        del correlation_id
        self.codes.append(code)
        return code


def _principal(*platform_roles: str) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        subject_id="platform-user",
        tenant_id="tenant-demo",
        roles=frozenset(),
        scopes=frozenset(),
        authentication_method="email_password",
        authenticated_at=datetime.now(UTC),
        correlation_id="test-correlation",
        platform_roles=frozenset(platform_roles),
    )


@pytest.mark.asyncio
async def test_platform_can_issue_one_click_workspace_signup_link() -> None:
    store = FakeOnboardingStore()
    service = SelfServiceTenantProvisioningService(
        store=store,  # type: ignore[arg-type]
        identity_store=object(),  # type: ignore[arg-type]
        password_hasher=object(),  # type: ignore[arg-type]
        rate_limits=object(),  # type: ignore[arg-type]
        token_secret="x" * 32,
        public_base_url="https://support.example.com",
    )

    result = await service.issue_workspace_onboarding_code(
        IssueWorkspaceOnboardingCodeCommand(
            principal=_principal("platform_operator"),
            target_email="Client@Example.com",
            expires_in_hours=72,
            site_limit=2,
            correlation_id="test-correlation",
        )
    )

    assert len(store.authorities) == 1
    assert len(store.policies) == 1
    assert store.policies[0].allowed_email_domains == ("example.com",)
    assert store.policies[0].site_limit == 2
    assert result.code.target_email == "client@example.com"
    assert result.signup_url.startswith("https://support.example.com/#signup_code=")
    assert "email=client%40example.com" in result.signup_url
    assert len(result.enrollment_code) == 26


@pytest.mark.asyncio
async def test_workspace_signup_code_requires_platform_role() -> None:
    service = SelfServiceTenantProvisioningService(
        store=FakeOnboardingStore(),  # type: ignore[arg-type]
        identity_store=object(),  # type: ignore[arg-type]
        password_hasher=object(),  # type: ignore[arg-type]
        rate_limits=object(),  # type: ignore[arg-type]
        token_secret="x" * 32,
        public_base_url="https://support.example.com",
    )

    with pytest.raises(PermissionError):
        await service.issue_workspace_onboarding_code(
            IssueWorkspaceOnboardingCodeCommand(
                principal=_principal("tenant_owner"),
                target_email="client@example.com",
                expires_in_hours=72,
                site_limit=1,
                correlation_id="test-correlation",
            )
        )
