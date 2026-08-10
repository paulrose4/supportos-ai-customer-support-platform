from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.models import AdminSession, AdminUser
from app.domain.rules.rbac import scopes_for_roles
from app.integrations.postgres.models import (
    AdminLoginThrottleModel,
    AdminSessionModel,
    AdminUserModel,
    AuditEventModel,
    EmailIdentityModel,
    IdentityUserModel,
    PlatformRoleAssignmentModel,
    TenantMembershipModel,
    TenantModel,
)


class PostgreSQLAdminIdentityStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_user_by_username(self, *, tenant_id: str, username: str) -> AdminUser | None:
        async with self._session_factory() as session:
            model = await session.scalar(
                select(AdminUserModel).where(
                    AdminUserModel.tenant_id == tenant_id,
                    AdminUserModel.username == username,
                )
            )
        return _to_user(model) if model is not None else None

    async def get_user_for_session(self, *, token_hash: str) -> AdminUser | None:
        now = datetime.now(UTC)
        async with self._session_factory.begin() as session:
            row = (
                await session.execute(
                    select(
                        IdentityUserModel,
                        TenantMembershipModel,
                        AdminSessionModel,
                        AdminUserModel,
                        EmailIdentityModel,
                    )
                    .join(
                        AdminSessionModel,
                        AdminSessionModel.user_id == IdentityUserModel.user_id,
                    )
                    .join(
                        TenantMembershipModel,
                        (TenantMembershipModel.tenant_id == AdminSessionModel.tenant_id)
                        & (TenantMembershipModel.user_id == AdminSessionModel.user_id),
                    )
                    .outerjoin(
                        AdminUserModel,
                        (AdminUserModel.tenant_id == AdminSessionModel.tenant_id)
                        & (AdminUserModel.global_user_id == AdminSessionModel.user_id),
                    )
                    .outerjoin(
                        EmailIdentityModel,
                        EmailIdentityModel.user_id == IdentityUserModel.user_id,
                    )
                    .where(
                        AdminSessionModel.token_hash == token_hash,
                        AdminSessionModel.revoked_at.is_(None),
                        AdminSessionModel.expires_at > now,
                        IdentityUserModel.status == "active",
                        TenantMembershipModel.status == "active",
                    )
                )
            ).first()
            if row is None:
                return None
            (
                user_model,
                membership_model,
                session_model,
                credential_model,
                email_identity_model,
            ) = row
            if session_model.last_seen_at < now - timedelta(minutes=1):
                session_model.last_seen_at = now
            platform_roles = frozenset(
                await session.scalars(
                    select(PlatformRoleAssignmentModel.role).where(
                        PlatformRoleAssignmentModel.user_id == user_model.user_id,
                        PlatformRoleAssignmentModel.status == "active",
                    )
                )
            )
            return _to_workspace_user(
                user_model=user_model,
                membership_model=membership_model,
                credential_model=credential_model,
                email_identity_model=email_identity_model,
                authentication_method=session_model.authentication_method,
                platform_roles=platform_roles,
            )

    async def get_user_by_id(self, *, tenant_id: str, user_id: str) -> AdminUser | None:
        async with self._session_factory() as session:
            model = await session.scalar(
                select(AdminUserModel).where(
                    AdminUserModel.tenant_id == tenant_id,
                    AdminUserModel.global_user_id == user_id,
                )
            )
        return _to_user(model) if model is not None else None

    async def list_users(self, *, tenant_id: str) -> list[AdminUser]:
        async with self._session_factory() as session:
            models = (
                await session.scalars(
                    select(AdminUserModel)
                    .where(AdminUserModel.tenant_id == tenant_id)
                    .order_by(AdminUserModel.created_at, AdminUserModel.username)
                )
            ).all()
        return [_to_user(model) for model in models]

    async def get_login_lock(
        self,
        *,
        source_fingerprint: str,
        checked_at: datetime,
    ) -> datetime | None:
        async with self._session_factory() as session:
            locked_until = await session.scalar(
                select(AdminLoginThrottleModel.locked_until).where(
                    AdminLoginThrottleModel.source_fingerprint == source_fingerprint,
                    AdminLoginThrottleModel.locked_until.is_not(None),
                    AdminLoginThrottleModel.locked_until > checked_at,
                )
            )
        return locked_until

    async def record_login_failure(
        self,
        *,
        source_fingerprint: str,
        tenant_id: str,
        username: str,
        correlation_id: str,
        occurred_at: datetime,
        max_attempts: int,
        window_seconds: int,
        lockout_seconds: int,
    ) -> datetime | None:
        try:
            return await self._record_login_failure(
                source_fingerprint=source_fingerprint,
                tenant_id=tenant_id,
                username=username,
                correlation_id=correlation_id,
                occurred_at=occurred_at,
                max_attempts=max_attempts,
                window_seconds=window_seconds,
                lockout_seconds=lockout_seconds,
            )
        except IntegrityError:
            return await self._record_login_failure(
                source_fingerprint=source_fingerprint,
                tenant_id=tenant_id,
                username=username,
                correlation_id=correlation_id,
                occurred_at=occurred_at,
                max_attempts=max_attempts,
                window_seconds=window_seconds,
                lockout_seconds=lockout_seconds,
            )

    async def _record_login_failure(
        self,
        *,
        source_fingerprint: str,
        tenant_id: str,
        username: str,
        correlation_id: str,
        occurred_at: datetime,
        max_attempts: int,
        window_seconds: int,
        lockout_seconds: int,
    ) -> datetime | None:
        async with self._session_factory.begin() as session:
            model = await session.scalar(
                select(AdminLoginThrottleModel)
                .where(AdminLoginThrottleModel.source_fingerprint == source_fingerprint)
                .with_for_update()
            )
            if model is None:
                model = AdminLoginThrottleModel(
                    source_fingerprint=source_fingerprint,
                    failure_count=0,
                    window_started_at=occurred_at,
                    last_failed_at=occurred_at,
                    locked_until=None,
                    last_tenant_id=tenant_id,
                    last_username=username,
                    created_at=occurred_at,
                    updated_at=occurred_at,
                )
                session.add(model)
                await session.flush()
            if model.window_started_at + timedelta(seconds=window_seconds) <= occurred_at:
                model.failure_count = 0
                model.window_started_at = occurred_at
                model.locked_until = None
            model.failure_count += 1
            model.last_failed_at = occurred_at
            model.last_tenant_id = tenant_id
            model.last_username = username
            model.updated_at = occurred_at
            if model.failure_count >= max_attempts:
                model.locked_until = occurred_at + timedelta(seconds=lockout_seconds)
            session.add(
                AuditEventModel(
                    tenant_id=tenant_id,
                    event_id=str(uuid4()),
                    event_type="admin_login.failed",
                    actor_subject_id=None,
                    correlation_id=correlation_id,
                    resource_type="admin_login_source",
                    resource_id=source_fingerprint,
                    details={
                        "username": username,
                        "failure_count": model.failure_count,
                        "locked_until": (
                            model.locked_until.isoformat() if model.locked_until else None
                        ),
                    },
                    created_at=occurred_at,
                )
            )
            return model.locked_until

    async def clear_login_failures(
        self,
        *,
        source_fingerprint: str,
        tenant_id: str,
        user_id: str,
        correlation_id: str,
        cleared_at: datetime,
    ) -> None:
        async with self._session_factory.begin() as session:
            result = await session.execute(
                delete(AdminLoginThrottleModel).where(
                    AdminLoginThrottleModel.source_fingerprint == source_fingerprint
                )
            )
            if result.rowcount:
                session.add(
                    AuditEventModel(
                        tenant_id=tenant_id,
                        event_id=str(uuid4()),
                        event_type="admin_login.throttle_cleared",
                        actor_subject_id=user_id,
                        correlation_id=correlation_id,
                        resource_type="admin_login_source",
                        resource_id=source_fingerprint,
                        details={},
                        created_at=cleared_at,
                    )
                )

    async def create_user_if_absent(self, user: AdminUser) -> AdminUser:
        try:
            async with self._session_factory.begin() as session:
                existing = await session.scalar(
                    select(AdminUserModel).where(
                        AdminUserModel.tenant_id == user.tenant_id,
                        AdminUserModel.username == user.username,
                    )
                )
                if existing is not None:
                    return _to_user(existing)
                await _ensure_local_identity(session, user)
                session.add(
                    AdminUserModel(
                        tenant_id=user.tenant_id,
                        user_id=user.user_id,
                        global_user_id=user.user_id,
                        username=user.username,
                        display_name=user.display_name,
                        password_hash=user.password_hash,
                        roles=sorted(user.roles),
                        scopes=sorted(user.scopes),
                        status=user.status,
                        created_at=user.created_at,
                        updated_at=user.updated_at,
                    )
                )
                session.add(
                    AuditEventModel(
                        tenant_id=user.tenant_id,
                        event_id=str(uuid4()),
                        event_type="admin_user.bootstrapped",
                        actor_subject_id="system",
                        resource_type="admin_user",
                        resource_id=user.user_id,
                        details={"username": user.username, "roles": sorted(user.roles)},
                        created_at=user.created_at,
                    )
                )
            return user
        except IntegrityError:
            existing = await self.get_user_by_username(
                tenant_id=user.tenant_id, username=user.username
            )
            if existing is None:
                raise
            return existing

    async def create_managed_user(
        self,
        user: AdminUser,
        *,
        actor_subject_id: str,
        correlation_id: str,
    ) -> AdminUser:
        try:
            async with self._session_factory.begin() as session:
                existing = await session.scalar(
                    select(AdminUserModel).where(
                        AdminUserModel.tenant_id == user.tenant_id,
                        AdminUserModel.username == user.username,
                    )
                )
                if existing is not None:
                    return _to_user(existing)
                await _ensure_local_identity(session, user)
                session.add(
                    AdminUserModel(
                        tenant_id=user.tenant_id,
                        user_id=user.user_id,
                        global_user_id=user.user_id,
                        username=user.username,
                        display_name=user.display_name,
                        password_hash=user.password_hash,
                        roles=sorted(user.roles),
                        scopes=sorted(user.scopes),
                        status=user.status,
                        created_at=user.created_at,
                        updated_at=user.updated_at,
                    )
                )
                session.add(
                    AuditEventModel(
                        tenant_id=user.tenant_id,
                        event_id=str(uuid5(NAMESPACE_URL, f"admin-user:{user.user_id}:created")),
                        event_type="admin_user.created",
                        actor_subject_id=actor_subject_id,
                        correlation_id=correlation_id,
                        resource_type="admin_user",
                        resource_id=user.user_id,
                        details={
                            "username": user.username,
                            "roles": sorted(user.roles),
                            "status": user.status,
                        },
                        created_at=user.created_at,
                    )
                )
            return user
        except IntegrityError:
            existing = await self.get_user_by_username(
                tenant_id=user.tenant_id, username=user.username
            )
            if existing is None:
                raise
            return existing

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
    ) -> AdminUser | None:
        async with self._session_factory.begin() as session:
            tenant_users = (
                await session.scalars(
                    select(AdminUserModel)
                    .where(AdminUserModel.tenant_id == tenant_id)
                    .order_by(AdminUserModel.user_id)
                    .with_for_update()
                )
            ).all()
            target = next((item for item in tenant_users if item.global_user_id == user_id), None)
            if target is None:
                return None
            removes_owner = (
                "tenant_owner" in target.roles
                and target.status == "active"
                and ("tenant_owner" not in roles or status != "active")
            )
            memberships = (
                await session.scalars(
                    select(TenantMembershipModel)
                    .where(TenantMembershipModel.tenant_id == tenant_id)
                    .with_for_update()
                )
            ).all()
            active_owner_count = sum(
                1
                for item in memberships
                if item.status == "active" and "tenant_owner" in item.roles
            )
            if removes_owner and active_owner_count <= 1:
                return None
            previous = {
                "display_name": target.display_name,
                "roles": sorted(target.roles),
                "status": target.status,
            }
            if (
                target.display_name == display_name
                and frozenset(target.roles) == roles
                and target.status == status
            ):
                return _to_user(target)
            security_changed = frozenset(target.roles) != roles or target.status != status
            target.display_name = display_name
            target.roles = sorted(roles)
            target.scopes = sorted(scopes)
            target.status = status
            target.updated_at = changed_at
            identity_user = await session.scalar(
                select(IdentityUserModel)
                .where(IdentityUserModel.user_id == user_id)
                .with_for_update()
            )
            membership = await session.scalar(
                select(TenantMembershipModel)
                .where(
                    TenantMembershipModel.tenant_id == tenant_id,
                    TenantMembershipModel.user_id == user_id,
                )
                .with_for_update()
            )
            if identity_user is not None:
                identity_user.display_name = display_name
                identity_user.updated_at = changed_at
            if membership is not None:
                membership.roles = sorted(roles)
                membership.scopes = sorted(scopes)
                membership.status = status
                membership.updated_at = changed_at
            revoked_count = 0
            if security_changed:
                revoked = await session.execute(
                    update(AdminSessionModel)
                    .where(
                        AdminSessionModel.tenant_id == tenant_id,
                        AdminSessionModel.user_id == user_id,
                        AdminSessionModel.revoked_at.is_(None),
                    )
                    .values(revoked_at=changed_at)
                )
                revoked_count = revoked.rowcount or 0
            session.add(
                AuditEventModel(
                    tenant_id=tenant_id,
                    event_id=str(uuid4()),
                    event_type="admin_user.updated",
                    actor_subject_id=actor_subject_id,
                    correlation_id=correlation_id,
                    resource_type="admin_user",
                    resource_id=user_id,
                    details={
                        "previous": previous,
                        "current": {
                            "display_name": display_name,
                            "roles": sorted(roles),
                            "status": status,
                        },
                        "revoked_session_count": revoked_count,
                    },
                    created_at=changed_at,
                )
            )
            await session.flush()
            return _to_user(target)

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
        async with self._session_factory.begin() as session:
            user = await session.scalar(
                select(AdminUserModel)
                .where(
                    AdminUserModel.tenant_id == tenant_id,
                    AdminUserModel.global_user_id == user_id,
                )
                .with_for_update()
            )
            if user is None or user.password_hash != expected_password_hash:
                return False
            user.password_hash = new_password_hash
            user.updated_at = changed_at
            revoked = await session.execute(
                update(AdminSessionModel)
                .where(
                    AdminSessionModel.tenant_id == tenant_id,
                    AdminSessionModel.user_id == user_id,
                    AdminSessionModel.revoked_at.is_(None),
                )
                .values(revoked_at=changed_at)
            )
            session.add(
                AuditEventModel(
                    tenant_id=tenant_id,
                    event_id=str(uuid4()),
                    event_type="admin_user.password_reset",
                    actor_subject_id=actor_subject_id,
                    correlation_id=correlation_id,
                    resource_type="admin_user",
                    resource_id=user_id,
                    details={"revoked_session_count": revoked.rowcount or 0},
                    created_at=changed_at,
                )
            )
            return True

    async def create_session(self, session: AdminSession) -> None:
        async with self._session_factory.begin() as database_session:
            database_session.add(
                AdminSessionModel(
                    tenant_id=session.tenant_id,
                    session_id=session.session_id,
                    user_id=session.user_id,
                    token_hash=session.token_hash,
                    expires_at=session.expires_at,
                    created_at=session.created_at,
                    last_seen_at=session.last_seen_at,
                    source_fingerprint=session.source_fingerprint,
                    authentication_method=session.authentication_method,
                    revoked_at=session.revoked_at,
                )
            )
            database_session.add(
                AuditEventModel(
                    tenant_id=session.tenant_id,
                    event_id=str(
                        uuid5(NAMESPACE_URL, f"admin-session:{session.session_id}:created")
                    ),
                    event_type="admin_session.created",
                    actor_subject_id=session.user_id,
                    resource_type="admin_session",
                    resource_id=session.session_id,
                    details={
                        "expires_at": session.expires_at.isoformat(),
                        "source_fingerprint_prefix": session.source_fingerprint[:12],
                    },
                    created_at=session.created_at,
                )
            )

    async def revoke_session(self, *, token_hash: str, revoked_at: datetime) -> bool:
        async with self._session_factory.begin() as session:
            model = await session.scalar(
                select(AdminSessionModel)
                .where(AdminSessionModel.token_hash == token_hash)
                .with_for_update()
            )
            if model is None or model.revoked_at is not None:
                return True
            model.revoked_at = revoked_at
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": model.tenant_id},
            )
            session.add(
                AuditEventModel(
                    tenant_id=model.tenant_id,
                    event_id=str(uuid4()),
                    event_type="admin_session.revoked",
                    actor_subject_id=model.user_id,
                    resource_type="admin_session",
                    resource_id=model.session_id,
                    details={},
                    created_at=revoked_at,
                )
            )
            return True

    async def list_user_sessions(
        self, *, tenant_id: str, user_id: str, limit: int = 50
    ) -> list[AdminSession]:
        async with self._session_factory() as session:
            models = list(
                await session.scalars(
                    select(AdminSessionModel)
                    .where(
                        AdminSessionModel.tenant_id == tenant_id,
                        AdminSessionModel.user_id == user_id,
                    )
                    .order_by(AdminSessionModel.created_at.desc())
                    .limit(max(1, min(limit, 100)))
                )
            )
        return [_to_session(model) for model in models]

    async def revoke_user_session(
        self,
        *,
        tenant_id: str,
        user_id: str,
        session_id: str,
        actor_subject_id: str,
        correlation_id: str,
        revoked_at: datetime,
    ) -> bool | None:
        async with self._session_factory.begin() as session:
            model = await session.scalar(
                select(AdminSessionModel)
                .where(
                    AdminSessionModel.tenant_id == tenant_id,
                    AdminSessionModel.user_id == user_id,
                    AdminSessionModel.session_id == session_id,
                )
                .with_for_update()
            )
            if model is None:
                return None
            if model.revoked_at is not None:
                return False
            model.revoked_at = revoked_at
            session.add(
                AuditEventModel(
                    tenant_id=tenant_id,
                    event_id=str(uuid5(NAMESPACE_URL, f"admin-session:{session_id}:self-revoked")),
                    event_type="admin_session.revoked",
                    actor_subject_id=actor_subject_id,
                    correlation_id=correlation_id,
                    resource_type="admin_session",
                    resource_id=session_id,
                    details={"revocation_mode": "self_service"},
                    created_at=revoked_at,
                )
            )
            return True

    async def change_password_and_revoke_sessions(
        self,
        *,
        tenant_id: str,
        user_id: str,
        expected_password_hash: str,
        new_password_hash: str,
        correlation_id: str,
        changed_at: datetime,
    ) -> bool:
        async with self._session_factory.begin() as session:
            user = await session.scalar(
                select(AdminUserModel)
                .where(
                    AdminUserModel.tenant_id == tenant_id,
                    AdminUserModel.global_user_id == user_id,
                )
                .with_for_update()
            )
            if user is None or user.password_hash != expected_password_hash:
                return False
            user.password_hash = new_password_hash
            user.updated_at = changed_at
            revoked = await session.execute(
                update(AdminSessionModel)
                .where(
                    AdminSessionModel.tenant_id == tenant_id,
                    AdminSessionModel.user_id == user_id,
                    AdminSessionModel.revoked_at.is_(None),
                )
                .values(revoked_at=changed_at)
            )
            session.add(
                AuditEventModel(
                    tenant_id=tenant_id,
                    event_id=str(uuid4()),
                    event_type="admin_user.password_changed",
                    actor_subject_id=user_id,
                    correlation_id=correlation_id,
                    resource_type="admin_user",
                    resource_id=user_id,
                    details={"revoked_session_count": revoked.rowcount or 0},
                    created_at=changed_at,
                )
            )
            return True


def _to_session(model: AdminSessionModel) -> AdminSession:
    return AdminSession(
        session_id=model.session_id,
        tenant_id=model.tenant_id,
        user_id=model.user_id,
        token_hash=model.token_hash,
        expires_at=model.expires_at,
        created_at=model.created_at,
        last_seen_at=model.last_seen_at,
        source_fingerprint=model.source_fingerprint,
        revoked_at=model.revoked_at,
        authentication_method=model.authentication_method,
    )


def _to_user(model: AdminUserModel) -> AdminUser:
    return AdminUser(
        user_id=model.global_user_id,
        tenant_id=model.tenant_id,
        username=model.username,
        display_name=model.display_name,
        password_hash=model.password_hash,
        roles=frozenset(model.roles),
        scopes=scopes_for_roles(frozenset(model.roles)),
        status=model.status,
        created_at=model.created_at,
        updated_at=model.updated_at,
        authentication_method="local",
    )


async def _ensure_local_identity(session: AsyncSession, user: AdminUser) -> None:
    tenant = await session.scalar(
        select(TenantModel).where(TenantModel.tenant_id == user.tenant_id)
    )
    if tenant is None:
        session.add(
            TenantModel(
                tenant_id=user.tenant_id,
                name=user.tenant_id,
                status="active",
                created_at=user.created_at,
                updated_at=user.updated_at,
            )
        )
    identity_user = await session.scalar(
        select(IdentityUserModel).where(IdentityUserModel.user_id == user.user_id)
    )
    if identity_user is None:
        session.add(
            IdentityUserModel(
                user_id=user.user_id,
                display_name=user.display_name,
                status=user.status,
                created_at=user.created_at,
                updated_at=user.updated_at,
            )
        )
    membership = await session.scalar(
        select(TenantMembershipModel).where(
            TenantMembershipModel.tenant_id == user.tenant_id,
            TenantMembershipModel.user_id == user.user_id,
        )
    )
    if membership is None:
        session.add(
            TenantMembershipModel(
                membership_id=str(
                    uuid5(NAMESPACE_URL, f"membership:{user.tenant_id}:{user.user_id}")
                ),
                tenant_id=user.tenant_id,
                user_id=user.user_id,
                roles=sorted(user.roles),
                scopes=sorted(user.scopes),
                status=user.status,
                created_at=user.created_at,
                updated_at=user.updated_at,
            )
        )


def _to_workspace_user(
    *,
    user_model: IdentityUserModel,
    membership_model: TenantMembershipModel,
    credential_model: AdminUserModel | None,
    email_identity_model: EmailIdentityModel | None,
    authentication_method: str,
    platform_roles: frozenset[str] = frozenset(),
) -> AdminUser:
    roles = frozenset(membership_model.roles)
    return AdminUser(
        user_id=user_model.user_id,
        tenant_id=membership_model.tenant_id,
        username=(
            email_identity_model.display_email
            if email_identity_model is not None
            else credential_model.username
            if credential_model is not None
            else user_model.user_id
        ),
        display_name=user_model.display_name,
        password_hash=(credential_model.password_hash if credential_model is not None else ""),
        roles=roles,
        scopes=scopes_for_roles(roles),
        status=membership_model.status,
        created_at=user_model.created_at,
        updated_at=max(user_model.updated_at, membership_model.updated_at),
        authentication_method=authentication_method,
        platform_roles=platform_roles,
    )
