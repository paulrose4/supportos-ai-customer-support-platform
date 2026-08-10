ADMIN_ROLE_SCOPES: dict[str, frozenset[str]] = {
    "tenant_owner": frozenset(
        {
            "knowledge:read",
            "knowledge:sync",
            "handoffs:read",
            "orders:read:self",
            "tickets:read:self",
            "sites:read",
            "sites:manage",
            "support:inbox:read",
            "support:inbox:write",
            "support:inbox:reply:self",
            "support:inbox:takeover",
            "support:inbox:routing",
            "support:inbox:resolve:self",
            "support:inbox:manage",
            "customers:memory:read",
            "customers:memory:write",
            "users:manage",
            "audit:read",
            "automation:manage",
            "automation:read",
            "customers:read",
        }
    ),
    "support_manager": frozenset(
        {
            "knowledge:read",
            "handoffs:read",
            "sites:read",
            "support:inbox:read",
            "support:inbox:write",
            "support:inbox:reply:self",
            "support:inbox:takeover",
            "support:inbox:routing",
            "support:inbox:resolve:self",
            "support:inbox:manage",
            "customers:memory:read",
            "customers:memory:write",
            "audit:read",
            "automation:manage",
            "automation:read",
            "customers:read",
        }
    ),
    "support_agent": frozenset(
        {
            "knowledge:read",
            "handoffs:read",
            "sites:read",
            "support:inbox:read",
            "support:inbox:write",
            "support:inbox:reply:self",
            "support:inbox:takeover",
            "support:inbox:resolve:self",
            "customers:memory:read",
            "customers:read",
        }
    ),
    "knowledge_admin": frozenset(
        {"knowledge:read", "knowledge:sync", "sites:read", "support:inbox:read"}
    ),
    "auditor": frozenset(
        {
            "audit:read",
            "customers:memory:read",
            "customers:read",
            "sites:read",
            "support:inbox:read",
        }
    ),
}


def scopes_for_roles(roles: frozenset[str]) -> frozenset[str]:
    scopes: set[str] = set()
    for role in roles:
        scopes.update(ADMIN_ROLE_SCOPES.get(role, ()))
    return frozenset(scopes)


ADMIN_ROLES = frozenset(ADMIN_ROLE_SCOPES)
