from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.application.dto import (
    CreateAdminUserCommand,
    ListAdminUsersQuery,
    ResetAdminUserPasswordCommand,
    UpdateAdminUserCommand,
)
from app.application.services import (
    AdminUserConflictError,
    AdminUserManagementService,
)
from app.domain.models import AdminUser, AuthenticatedPrincipal
from app.domain.rules.rbac import scopes_for_roles


class FakePasswordHasher:
    def hash_password(self, password: str) -> str:
        return f"hashed:{password}"

    def verify_password(self, password: str, encoded_hash: str) -> bool:
        return encoded_hash == f"hashed:{password}"


class InMemoryManagedUserStore:
    def __init__(self, users: list[AdminUser] | None = None) -> None:
        self.users = {(item.tenant_id, item.user_id): item for item in users or []}
        self.audit_events: list[str] = []
        self.revoked_users: list[str] = []

    async def get_user_by_username(self, *, tenant_id: str, username: str):  # type: ignore[no-untyped-def]
        return next(
            (
                item
                for item in self.users.values()
                if item.tenant_id == tenant_id and item.username == username
            ),
            None,
        )

    async def get_user_by_id(self, *, tenant_id: str, user_id: str):  # type: ignore[no-untyped-def]
        return self.users.get((tenant_id, user_id))

    async def list_users(self, *, tenant_id: str):  # type: ignore[no-untyped-def]
        return sorted(
            (item for item in self.users.values() if item.tenant_id == tenant_id),
            key=lambda item: item.username,
        )

    async def create_managed_user(
        self, user: AdminUser, *, actor_subject_id: str, correlation_id: str
    ) -> AdminUser:
        del actor_subject_id, correlation_id
        self.users.setdefault((user.tenant_id, user.user_id), user)
        self.audit_events.append("admin_user.created")
        return self.users[(user.tenant_id, user.user_id)]

    async def update_managed_user(
        self,
        *,
        tenant_id: str,
        user_id: str,
        display_name: str,
        roles: frozenset[str],
        scopes: frozenset[str],
        status: str,
        actor_subject_id: str,
        correlation_id: str,
        changed_at: datetime,
    ):  # type: ignore[no-untyped-def]
        del actor_subject_id, correlation_id
        current = self.users.get((tenant_id, user_id))
        if current is None:
            return None
        active_owners = [
            item
            for item in self.users.values()
            if item.tenant_id == tenant_id
            and item.status == "active"
            and "tenant_owner" in item.roles
        ]
        removes_last_owner = (
            current in active_owners
            and len(active_owners) == 1
            and (status != "active" or "tenant_owner" not in roles)
        )
        if removes_last_owner:
            return None
        if (
            current.display_name == display_name
            and current.roles == roles
            and current.status == status
        ):
            return current
        updated = replace(
            current,
            display_name=display_name,
            roles=roles,
            scopes=scopes,
            status=status,
            updated_at=changed_at,
        )
        self.users[(tenant_id, user_id)] = updated
        self.audit_events.append("admin_user.updated")
        if current.roles != roles or current.status != status:
            self.revoked_users.append(user_id)
        return updated

    async def reset_managed_password(
        self,
        *,
        tenant_id: str,
        user_id: str,
        expected_password_hash: str,
        new_password_hash: str,
        actor_subject_id: str,
        correlation_id: str,
        changed_at: datetime,
    ) -> bool:
        del actor_subject_id, correlation_id
        current = self.users.get((tenant_id, user_id))
        if current is None or current.password_hash != expected_password_hash:
            return False
        self.users[(tenant_id, user_id)] = replace(
            current, password_hash=new_password_hash, updated_at=changed_at
        )
        self.audit_events.append("admin_user.password_reset")
        self.revoked_users.append(user_id)
        return True


def user(
    user_id: str,
    username: str,
    roles: frozenset[str],
    *,
    tenant_id: str = "tenant-a",
) -> AdminUser:
    now = datetime.now(UTC)
    return AdminUser(
        user_id=user_id,
        tenant_id=tenant_id,
        username=username,
        display_name=username.title(),
        password_hash="hashed:initial-password",
        roles=roles,
        scopes=scopes_for_roles(roles),
        status="active",
        created_at=now,
        updated_at=now,
    )


def principal(
    *,
    subject_id: str = "owner-1",
    tenant_id: str = "tenant-a",
    roles: frozenset[str] = frozenset({"tenant_owner"}),
) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        subject_id=subject_id,
        tenant_id=tenant_id,
        roles=roles,
        scopes=scopes_for_roles(roles),
        authentication_method="admin_session",
        authenticated_at=datetime.now(UTC),
        correlation_id="correlation-1",
    )


async def test_owner_creates_user_idempotently_and_lists_only_own_tenant() -> None:
    store = InMemoryManagedUserStore(
        [
            user("owner-1", "owner", frozenset({"tenant_owner"})),
            user("other", "other", frozenset({"tenant_owner"}), tenant_id="tenant-b"),
        ]
    )
    service = AdminUserManagementService(identity_store=store, password_hasher=FakePasswordHasher())
    command = CreateAdminUserCommand(
        principal=principal(),
        username=" Agent ",
        display_name="Support Agent",
        password="temporary-password",
        roles=frozenset({"support_agent"}),
        correlation_id="correlation-create",
    )

    first = await service.create_user(command)
    second = await service.create_user(command)
    listed = await service.list_users(ListAdminUsersQuery(principal()))

    assert first.user_id == second.user_id
    assert [item.username for item in listed.items] == ["agent", "owner"]
    assert store.audit_events.count("admin_user.created") == 1


async def test_non_owner_cannot_manage_users() -> None:
    service = AdminUserManagementService(
        identity_store=InMemoryManagedUserStore(), password_hasher=FakePasswordHasher()
    )

    with pytest.raises(PermissionError, match="management permission"):
        await service.list_users(
            ListAdminUsersQuery(principal(roles=frozenset({"support_manager"})))
        )


async def test_update_revokes_sessions_and_protects_last_owner() -> None:
    owner = user("owner-1", "owner", frozenset({"tenant_owner"}))
    agent = user("agent-1", "agent", frozenset({"support_agent"}))
    store = InMemoryManagedUserStore([owner, agent])
    service = AdminUserManagementService(identity_store=store, password_hasher=FakePasswordHasher())

    updated = await service.update_user(
        UpdateAdminUserCommand(
            principal=principal(),
            user_id="agent-1",
            display_name="Manager",
            roles=frozenset({"support_manager"}),
            status="active",
            correlation_id="correlation-update",
        )
    )
    with pytest.raises(LookupError, match="last active owner"):
        await service.update_user(
            UpdateAdminUserCommand(
                principal=principal(subject_id="different-owner"),
                user_id="owner-1",
                display_name="Owner",
                roles=frozenset({"support_agent"}),
                status="active",
                correlation_id="correlation-owner",
            )
        )

    assert updated.roles == frozenset({"support_manager"})
    assert store.revoked_users == ["agent-1"]


async def test_owner_cannot_disable_self() -> None:
    store = InMemoryManagedUserStore([user("owner-1", "owner", frozenset({"tenant_owner"}))])
    service = AdminUserManagementService(identity_store=store, password_hasher=FakePasswordHasher())

    with pytest.raises(AdminUserConflictError, match="disable your own"):
        await service.update_user(
            UpdateAdminUserCommand(
                principal=principal(),
                user_id="owner-1",
                display_name="Owner",
                roles=frozenset({"tenant_owner"}),
                status="disabled",
                correlation_id="correlation-disable",
            )
        )


async def test_password_reset_is_retry_safe_and_revokes_sessions_once() -> None:
    agent = user("agent-1", "agent", frozenset({"support_agent"}))
    store = InMemoryManagedUserStore([agent])
    service = AdminUserManagementService(identity_store=store, password_hasher=FakePasswordHasher())
    command = ResetAdminUserPasswordCommand(
        principal=principal(),
        user_id="agent-1",
        new_password="replacement-password",
        correlation_id="correlation-reset",
    )

    await service.reset_password(command)
    await service.reset_password(command)

    assert store.audit_events == ["admin_user.password_reset"]
    assert store.revoked_users == ["agent-1"]
