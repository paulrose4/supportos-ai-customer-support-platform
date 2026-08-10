from datetime import UTC, datetime

from app.domain.models import AuthenticatedPrincipal


class MockAuthenticationAdapter:
    def __init__(self, *, subject_id: str, tenant_id: str) -> None:
        self._subject_id = subject_id
        self._tenant_id = tenant_id

    async def authenticate(self, correlation_id: str) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(
            subject_id=self._subject_id,
            tenant_id=self._tenant_id,
            roles=frozenset({"customer", "knowledge_admin", "support_agent"}),
            scopes=frozenset(
                {
                    "knowledge:read",
                    "knowledge:sync",
                    "knowledge:sync:global",
                    "handoffs:read",
                    "orders:read:self",
                    "tickets:read:self",
                    "sites:read",
                    "support:inbox:read",
                    "support:inbox:write",
                    "customers:memory:read",
                    "customers:memory:write",
                }
            ),
            authentication_method="mock",
            authenticated_at=datetime.now(UTC),
            correlation_id=correlation_id,
        )
