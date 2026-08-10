from app.application.dto.analytics import GetSupportAnalyticsQuery, SupportAnalyticsResult
from app.domain.ports import SupportAnalyticsPort


class SupportAnalyticsService:
    def __init__(self, analytics: SupportAnalyticsPort) -> None:
        self._analytics = analytics

    async def overview(self, query: GetSupportAnalyticsQuery) -> SupportAnalyticsResult:
        if "support:inbox:read" not in query.principal.scopes:
            raise PermissionError("scope access denied")
        if not 1 <= query.days <= 365:
            raise ValueError("analytics days must be between 1 and 365")
        snapshot = await self._analytics.overview(
            tenant_id=query.principal.tenant_id,
            days=query.days,
            site_id=query.site_id,
        )
        return SupportAnalyticsResult(snapshot)
