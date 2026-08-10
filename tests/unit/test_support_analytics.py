from datetime import UTC, datetime

import pytest

from app.application.dto import GetSupportAnalyticsQuery
from app.application.services import SupportAnalyticsService
from app.domain.models import AuthenticatedPrincipal
from app.domain.ports import SupportAnalyticsSnapshot


class FakeAnalyticsPort:
    async def overview(self, *, tenant_id: str, days: int, site_id: str | None):  # type: ignore[no-untyped-def]
        assert tenant_id == "tenant-a"
        return SupportAnalyticsSnapshot(days, site_id, 10, 12, 8, 4, 3, 6)


def principal(scopes: frozenset[str]) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        subject_id="agent-1",
        tenant_id="tenant-a",
        roles=frozenset({"support_agent"}),
        scopes=scopes,
        authentication_method="session",
        authenticated_at=datetime.now(UTC),
        correlation_id="correlation-1",
    )


@pytest.mark.asyncio
async def test_support_analytics_uses_trusted_tenant_and_filters() -> None:
    result = await SupportAnalyticsService(FakeAnalyticsPort()).overview(
        GetSupportAnalyticsQuery(
            principal=principal(frozenset({"support:inbox:read"})),
            days=30,
            site_id="site-a",
        )
    )

    assert result.snapshot.ai_answers == 8
    assert result.snapshot.site_id == "site-a"


@pytest.mark.asyncio
async def test_support_analytics_requires_scope() -> None:
    with pytest.raises(PermissionError):
        await SupportAnalyticsService(FakeAnalyticsPort()).overview(
            GetSupportAnalyticsQuery(principal=principal(frozenset()))
        )
