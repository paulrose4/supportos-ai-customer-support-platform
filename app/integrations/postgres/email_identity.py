from datetime import datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.models import (
    AdminSession,
    AdminUser,
    EmailIdentity,
    EmailLoginAccount,
    IdentityUser,
    LoginCompletionGrant,
    PasswordResetToken,
    TenantInvitation,
    TenantMembership,
)
from app.domain.rules.rbac import scopes_for_roles
from app.integrations.postgres.models import (
    AdminSessionModel,
    AdminUserModel,
    AuditEventModel,
    EmailIdentityModel,
    EmailLoginThrottleModel,
    IdentityUserModel,
    LoginCompletionGrantModel,
    PasswordCredentialModel,
    PasswordResetTokenModel,
    PlatformAuditEventModel,
    PlatformRoleAssignmentModel,
    TenantInvitationModel,
    TenantMembershipModel,
    TenantModel,
)


class PostgreSQLEmailIdentityStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_login_account(self, *, normalized_email: str) -> EmailLoginAccount | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(IdentityUserModel, EmailIdentityModel, PasswordCredentialModel)
                    .join(
                        EmailIdentityModel,
                        EmailIdentityModel.user_id == IdentityUserModel.user_id,
                    )
                    .join(
                        PasswordCredentialModel,
                        PasswordCredentialModel.user_id == IdentityUserModel.user_id,
                    )
                    .where(EmailIdentityModel.normalized_email == normalized_email)
                )
            ).first()
        return _to_account(row)

    async def get_login_account_for_session(self, *, token_hash: str) -> EmailLoginAccount | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(IdentityUserModel, EmailIdentityModel, PasswordCredentialModel)
                    .join(
                        AdminSessionModel,
                        AdminSessionModel.user_id == IdentityUserModel.user_id,
                    )
                    .join(
                        EmailIdentityModel,
                        EmailIdentityModel.user_id == IdentityUserModel.user_id,
                    )
                    .join(
                        PasswordCredentialModel,
                        PasswordCredentialModel.user_id == IdentityUserModel.user_id,
                    )
                    .where(
                        AdminSessionModel.token_hash == token_hash,
                        AdminSessionModel.revoked_at.is_(None),
                        AdminSessionModel.authentication_method == "email_password",
                    )
                )
            ).first()
        return _to_account(row)

    async def get_email_identity_by_user_id(self, *, user_id: str) -> EmailIdentity | None:
        async with self._session_factory() as session:
            model = await session.scalar(
                select(EmailIdentityModel).where(EmailIdentityModel.user_id == user_id)
            )
        return _to_email_identity(model) if model is not None else None

    async def get_login_lock(
        self,
        *,
        source_fingerprint: str,
        email_hash: str,
        checked_at: datetime,
    ) -> datetime | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(EmailLoginThrottleModel.locked_until).where(
                    EmailLoginThrottleModel.source_fingerprint == source_fingerprint,
                    EmailLoginThrottleModel.email_hash == email_hash,
                    EmailLoginThrottleModel.locked_until.is_not(None),
                    EmailLoginThrottleModel.locked_until > checked_at,
                )
            )

    async def record_login_failure(
        self,
        *,
        source_fingerprint: str,
        email_hash: str,
        correlation_id: str,
        occurred_at: datetime,
        max_attempts: int,
        window_seconds: int,
        lockout_seconds: int,
    ) -> datetime | None:
        try:
            return await self._record_login_failure(
                source_fingerprint=source_fingerprint,
                email_hash=email_hash,
                correlation_id=correlation_id,
                occurred_at=occurred_at,
                max_attempts=max_attempts,
                window_seconds=window_seconds,
                lockout_seconds=lockout_seconds,
            )
        except IntegrityError:
            return await self._record_login_failure(
                source_fingerprint=source_fingerprint,
                email_hash=email_hash,
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
        email_hash: str,
        correlation_id: str,
        occurred_at: datetime,
        max_attempts: int,
        window_seconds: int,
        lockout_seconds: int,
    ) -> datetime | None:
        async with self._session_factory.begin() as session:
            model = await session.scalar(
                select(EmailLoginThrottleModel)
                .where(
                    EmailLoginThrottleModel.source_fingerprint == source_fingerprint,
                    EmailLoginThrottleModel.email_hash == email_hash,
                )
                .with_for_update()
            )
            if model is None:
                model = EmailLoginThrottleModel(
                    source_fingerprint=source_fingerprint,
                    email_hash=email_hash,
                    failure_count=0,
                    window_started_at=occurred_at,
                    last_failed_at=occurred_at,
                    locked_until=None,
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
            model.updated_at = occurred_at
            if model.failure_count >= max_attempts:
                model.locked_until = occurred_at + timedelta(seconds=lockout_seconds)
            session.add(
                PlatformAuditEventModel(
                    event_id=str(uuid4()),
                    event_type="email_login.failed",
                    actor_subject_id=None,
                    correlation_id=correlation_id,
                    resource_type="email_login_source",
                    resource_id=f"{source_fingerprint[:12]}:{email_hash[:12]}",
                    details={
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
        email_hash: str,
        user_id: str,
        correlation_id: str,
        cleared_at: datetime,
    ) -> None:
        async with self._session_factory.begin() as session:
            result = await session.execute(
                delete(EmailLoginThrottleModel).where(
                    EmailLoginThrottleModel.source_fingerprint == source_fingerprint,
                    EmailLoginThrottleModel.email_hash == email_hash,
                )
            )
            if result.rowcount:
                session.add(
                    PlatformAuditEventModel(
                        event_id=str(uuid4()),
                        event_type="email_login.throttle_cleared",
                        actor_subject_id=user_id,
                        correlation_id=correlation_id,
                        resource_type="email_login_source",
                        resource_id=f"{source_fingerprint[:12]}:{email_hash[:12]}",
                        details={},
                        created_at=cleared_at,
                    )
                )

    async def create_invitation(
        self,
        invitation: TenantInvitation,
        *,
        correlation_id: str,
    ) -> TenantInvitation:
        async with self._session_factory.begin() as session:
            tenant = await session.scalar(
                select(TenantModel)
                .where(TenantModel.tenant_id == invitation.tenant_id)
                .with_for_update()
            )
            if tenant is None or tenant.status != "active":
                raise LookupError("workspace was not found or is inactive")
            existing = list(
                await session.scalars(
                    select(TenantInvitationModel)
                    .where(
                        TenantInvitationModel.tenant_id == invitation.tenant_id,
                        TenantInvitationModel.normalized_email == invitation.normalized_email,
                        TenantInvitationModel.status == "pending",
                    )
                    .with_for_update()
                )
            )
            for previous in existing:
                previous.status = "revoked"
                previous.revoked_by = invitation.created_by
                previous.revoked_at = invitation.created_at
            model = TenantInvitationModel(
                invitation_id=invitation.invitation_id,
                tenant_id=invitation.tenant_id,
                normalized_email=invitation.normalized_email,
                display_email=invitation.display_email,
                roles=sorted(invitation.roles),
                token_hash=invitation.token_hash,
                status=invitation.status,
                expires_at=invitation.expires_at,
                created_by=invitation.created_by,
                created_at=invitation.created_at,
                redeemed_by=None,
                redeemed_at=None,
                revoked_by=None,
                revoked_at=None,
            )
            session.add(model)
            session.add(
                PlatformAuditEventModel(
                    event_id=str(uuid4()),
                    event_type="invitation.created",
                    actor_subject_id=invitation.created_by,
                    correlation_id=correlation_id,
                    resource_type="tenant_invitation",
                    resource_id=invitation.invitation_id,
                    details={
                        "tenant_id": invitation.tenant_id,
                        "email_hash_prefix": invitation.token_hash[:12],
                        "roles": sorted(invitation.roles),
                        "expires_at": invitation.expires_at.isoformat(),
                        "replaced_pending_count": len(existing),
                    },
                    created_at=invitation.created_at,
                )
            )
            return _to_invitation(model, tenant.name)

    async def get_invitation_by_token(
        self, *, token_hash: str, checked_at: datetime
    ) -> TenantInvitation | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(TenantInvitationModel, TenantModel)
                    .join(
                        TenantModel,
                        TenantModel.tenant_id == TenantInvitationModel.tenant_id,
                    )
                    .where(
                        TenantInvitationModel.token_hash == token_hash,
                        TenantInvitationModel.status == "pending",
                        TenantInvitationModel.expires_at > checked_at,
                        TenantModel.status == "active",
                    )
                )
            ).first()
        return _to_invitation(row[0], row[1].name) if row is not None else None

    async def list_invitations(self, *, tenant_id: str, limit: int = 100) -> list[TenantInvitation]:
        async with self._session_factory() as session:
            tenant = await session.scalar(
                select(TenantModel).where(TenantModel.tenant_id == tenant_id)
            )
            if tenant is None:
                return []
            models = list(
                await session.scalars(
                    select(TenantInvitationModel)
                    .where(TenantInvitationModel.tenant_id == tenant_id)
                    .order_by(TenantInvitationModel.created_at.desc())
                    .limit(limit)
                )
            )
        return [_to_invitation(model, tenant.name) for model in models]

    async def revoke_invitation(
        self,
        *,
        tenant_id: str,
        invitation_id: str,
        actor_subject_id: str,
        correlation_id: str,
        revoked_at: datetime,
    ) -> TenantInvitation | None:
        async with self._session_factory.begin() as session:
            row = (
                await session.execute(
                    select(TenantInvitationModel, TenantModel)
                    .join(
                        TenantModel,
                        TenantModel.tenant_id == TenantInvitationModel.tenant_id,
                    )
                    .where(
                        TenantInvitationModel.tenant_id == tenant_id,
                        TenantInvitationModel.invitation_id == invitation_id,
                    )
                    .with_for_update(of=TenantInvitationModel)
                )
            ).first()
            if row is None:
                return None
            model, tenant = row
            if model.status == "pending":
                model.status = "revoked"
                model.revoked_by = actor_subject_id
                model.revoked_at = revoked_at
                session.add(
                    PlatformAuditEventModel(
                        event_id=str(uuid4()),
                        event_type="invitation.revoked",
                        actor_subject_id=actor_subject_id,
                        correlation_id=correlation_id,
                        resource_type="tenant_invitation",
                        resource_id=invitation_id,
                        details={"tenant_id": tenant_id},
                        created_at=revoked_at,
                    )
                )
            return _to_invitation(model, tenant.name)

    async def redeem_invitation(
        self,
        *,
        token_hash: str,
        display_name: str,
        password_hash: str | None,
        existing_user_id: str | None,
        correlation_id: str,
        redeemed_at: datetime,
    ) -> EmailLoginAccount | None:
        async with self._session_factory.begin() as session:
            row = (
                await session.execute(
                    select(TenantInvitationModel, TenantModel)
                    .join(
                        TenantModel,
                        TenantModel.tenant_id == TenantInvitationModel.tenant_id,
                    )
                    .where(
                        TenantInvitationModel.token_hash == token_hash,
                        TenantInvitationModel.status == "pending",
                        TenantInvitationModel.expires_at > redeemed_at,
                        TenantModel.status == "active",
                    )
                    .with_for_update(of=TenantInvitationModel)
                )
            ).first()
            if row is None:
                return None
            invitation, tenant = row

            if existing_user_id is not None:
                account_row = (
                    await session.execute(
                        select(
                            IdentityUserModel,
                            EmailIdentityModel,
                            PasswordCredentialModel,
                        )
                        .join(
                            EmailIdentityModel,
                            EmailIdentityModel.user_id == IdentityUserModel.user_id,
                        )
                        .join(
                            PasswordCredentialModel,
                            PasswordCredentialModel.user_id == IdentityUserModel.user_id,
                        )
                        .where(
                            IdentityUserModel.user_id == existing_user_id,
                            EmailIdentityModel.normalized_email == invitation.normalized_email,
                        )
                        .with_for_update(of=IdentityUserModel)
                    )
                ).first()
                if account_row is None:
                    return None
                user_model, email_model, credential_model = account_row
            else:
                if password_hash is None:
                    return None
                conflicting_email = await session.scalar(
                    select(EmailIdentityModel)
                    .where(EmailIdentityModel.normalized_email == invitation.normalized_email)
                    .with_for_update()
                )
                if conflicting_email is not None:
                    return None
                user_model = IdentityUserModel(
                    user_id=str(uuid4()),
                    display_name=display_name,
                    status="active",
                    created_at=redeemed_at,
                    updated_at=redeemed_at,
                )
                email_model = EmailIdentityModel(
                    identity_id=str(uuid4()),
                    user_id=user_model.user_id,
                    normalized_email=invitation.normalized_email,
                    display_email=invitation.display_email,
                    status="active",
                    verified_at=redeemed_at,
                    created_at=redeemed_at,
                    updated_at=redeemed_at,
                )
                credential_model = PasswordCredentialModel(
                    user_id=user_model.user_id,
                    password_hash=password_hash,
                    password_version=1,
                    changed_at=redeemed_at,
                    created_at=redeemed_at,
                )
                session.add(user_model)
                await session.flush()
                session.add_all((email_model, credential_model))
                await session.flush()

            membership = await session.scalar(
                select(TenantMembershipModel)
                .where(
                    TenantMembershipModel.tenant_id == invitation.tenant_id,
                    TenantMembershipModel.user_id == user_model.user_id,
                )
                .with_for_update()
            )
            roles = frozenset(invitation.roles)
            if membership is None:
                membership = TenantMembershipModel(
                    membership_id=str(
                        uuid5(
                            NAMESPACE_URL,
                            f"membership:{invitation.tenant_id}:{user_model.user_id}",
                        )
                    ),
                    tenant_id=invitation.tenant_id,
                    user_id=user_model.user_id,
                    roles=sorted(roles),
                    scopes=sorted(scopes_for_roles(roles)),
                    status="active",
                    source="invitation",
                    approval_status="approved",
                    activated_at=redeemed_at,
                    deactivated_at=None,
                    created_by=invitation.created_by,
                    created_at=redeemed_at,
                    updated_at=redeemed_at,
                )
                session.add(membership)
            else:
                membership.roles = sorted(roles)
                membership.scopes = sorted(scopes_for_roles(roles))
                membership.status = "active"
                membership.source = "invitation"
                membership.approval_status = "approved"
                membership.activated_at = redeemed_at
                membership.deactivated_at = None
                membership.updated_at = redeemed_at

            invitation.status = "redeemed"
            invitation.redeemed_by = user_model.user_id
            invitation.redeemed_at = redeemed_at
            session.add(
                PlatformAuditEventModel(
                    event_id=str(uuid4()),
                    event_type="invitation.redeemed",
                    actor_subject_id=user_model.user_id,
                    correlation_id=correlation_id,
                    resource_type="tenant_invitation",
                    resource_id=invitation.invitation_id,
                    details={
                        "tenant_id": invitation.tenant_id,
                        "membership_id": membership.membership_id,
                    },
                    created_at=redeemed_at,
                )
            )
            return EmailLoginAccount(
                user=IdentityUser(
                    user_id=user_model.user_id,
                    display_name=user_model.display_name,
                    status=user_model.status,
                    created_at=user_model.created_at,
                    updated_at=user_model.updated_at,
                    primary_email=email_model.display_email,
                ),
                identity=_to_email_identity(email_model),
                password_hash=credential_model.password_hash,
                password_version=credential_model.password_version,
            )

    async def save_login_completion_grant(self, grant: LoginCompletionGrant) -> None:
        async with self._session_factory.begin() as session:
            session.add(
                LoginCompletionGrantModel(
                    grant_id=grant.grant_id,
                    user_id=grant.user_id,
                    token_hash=grant.token_hash,
                    expires_at=grant.expires_at,
                    created_at=grant.created_at,
                    consumed_at=grant.consumed_at,
                    authentication_method=grant.authentication_method,
                )
            )

    async def list_active_memberships(self, *, user_id: str) -> list[TenantMembership]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(TenantMembershipModel, TenantModel)
                    .join(
                        TenantModel,
                        TenantModel.tenant_id == TenantMembershipModel.tenant_id,
                    )
                    .where(
                        TenantMembershipModel.user_id == user_id,
                        TenantMembershipModel.status == "active",
                        TenantModel.status == "active",
                    )
                    .order_by(TenantModel.name)
                )
            ).all()
        return [
            TenantMembership(
                membership_id=membership.membership_id,
                tenant_id=membership.tenant_id,
                tenant_name=tenant.name,
                user_id=membership.user_id,
                roles=frozenset(membership.roles),
                scopes=scopes_for_roles(frozenset(membership.roles)),
                status=membership.status,
                created_at=membership.created_at,
                updated_at=membership.updated_at,
            )
            for membership, tenant in rows
        ]

    async def create_session_for_workspace(self, session: AdminSession) -> AdminUser | None:
        async with self._session_factory.begin() as database_session:
            row = (
                await database_session.execute(
                    select(
                        IdentityUserModel,
                        TenantMembershipModel,
                        TenantModel,
                        EmailIdentityModel,
                        AdminUserModel,
                    )
                    .join(
                        TenantMembershipModel,
                        TenantMembershipModel.user_id == IdentityUserModel.user_id,
                    )
                    .join(
                        TenantModel,
                        TenantModel.tenant_id == TenantMembershipModel.tenant_id,
                    )
                    .outerjoin(
                        EmailIdentityModel,
                        EmailIdentityModel.user_id == IdentityUserModel.user_id,
                    )
                    .outerjoin(
                        AdminUserModel,
                        (AdminUserModel.tenant_id == TenantMembershipModel.tenant_id)
                        & (AdminUserModel.global_user_id == IdentityUserModel.user_id),
                    )
                    .where(
                        IdentityUserModel.user_id == session.user_id,
                        IdentityUserModel.status == "active",
                        TenantMembershipModel.tenant_id == session.tenant_id,
                        TenantMembershipModel.status == "active",
                        TenantModel.status == "active",
                    )
                    .with_for_update(of=(IdentityUserModel, TenantMembershipModel))
                )
            ).first()
            if row is None:
                return None
            user, membership, _tenant, email_identity, legacy_credential = row
            platform_roles = frozenset(
                await database_session.scalars(
                    select(PlatformRoleAssignmentModel.role).where(
                        PlatformRoleAssignmentModel.user_id == user.user_id,
                        PlatformRoleAssignmentModel.status == "active",
                    )
                )
            )
            await _set_transaction_tenant(database_session, session.tenant_id)
            _add_session(database_session, session)
            roles = frozenset(membership.roles)
            return AdminUser(
                user_id=user.user_id,
                tenant_id=membership.tenant_id,
                username=(
                    email_identity.display_email
                    if email_identity is not None
                    else legacy_credential.username
                    if legacy_credential is not None
                    else user.user_id
                ),
                display_name=user.display_name,
                password_hash="",
                roles=roles,
                scopes=scopes_for_roles(roles),
                status=membership.status,
                created_at=user.created_at,
                updated_at=max(user.updated_at, membership.updated_at),
                authentication_method=session.authentication_method,
                platform_roles=platform_roles,
            )

    async def create_password_reset(self, token: PasswordResetToken) -> None:
        async with self._session_factory.begin() as session:
            await session.execute(
                update(PasswordResetTokenModel)
                .where(
                    PasswordResetTokenModel.user_id == token.user_id,
                    PasswordResetTokenModel.consumed_at.is_(None),
                )
                .values(consumed_at=token.created_at)
            )
            session.add(
                PasswordResetTokenModel(
                    reset_id=token.reset_id,
                    user_id=token.user_id,
                    token_hash=token.token_hash,
                    expires_at=token.expires_at,
                    created_at=token.created_at,
                    consumed_at=token.consumed_at,
                )
            )
            session.add(
                PlatformAuditEventModel(
                    event_id=str(uuid4()),
                    event_type="email_password.reset_requested",
                    actor_subject_id=token.user_id,
                    correlation_id=None,
                    resource_type="identity_user",
                    resource_id=token.user_id,
                    details={"expires_at": token.expires_at.isoformat()},
                    created_at=token.created_at,
                )
            )

    async def reset_password(
        self,
        *,
        token_hash: str,
        new_password_hash: str,
        correlation_id: str,
        changed_at: datetime,
    ) -> bool:
        async with self._session_factory.begin() as session:
            token = await session.scalar(
                select(PasswordResetTokenModel)
                .where(
                    PasswordResetTokenModel.token_hash == token_hash,
                    PasswordResetTokenModel.consumed_at.is_(None),
                    PasswordResetTokenModel.expires_at > changed_at,
                )
                .with_for_update()
            )
            if token is None:
                return False
            credential = await session.scalar(
                select(PasswordCredentialModel)
                .where(PasswordCredentialModel.user_id == token.user_id)
                .with_for_update()
            )
            if credential is None:
                return False
            credential.password_hash = new_password_hash
            credential.password_version += 1
            credential.changed_at = changed_at
            token.consumed_at = changed_at
            revoked = await session.execute(
                update(AdminSessionModel)
                .where(
                    AdminSessionModel.user_id == token.user_id,
                    AdminSessionModel.revoked_at.is_(None),
                )
                .values(revoked_at=changed_at)
            )
            session.add(
                PlatformAuditEventModel(
                    event_id=str(uuid4()),
                    event_type="email_password.reset_completed",
                    actor_subject_id=token.user_id,
                    correlation_id=correlation_id,
                    resource_type="identity_user",
                    resource_id=token.user_id,
                    details={"revoked_session_count": revoked.rowcount or 0},
                    created_at=changed_at,
                )
            )
            return True

    async def change_password_from_session(
        self,
        *,
        session_token_hash: str,
        expected_password_hash: str,
        new_password_hash: str,
        correlation_id: str,
        changed_at: datetime,
    ) -> bool:
        async with self._session_factory.begin() as session:
            current_session = await session.scalar(
                select(AdminSessionModel)
                .where(
                    AdminSessionModel.token_hash == session_token_hash,
                    AdminSessionModel.revoked_at.is_(None),
                    AdminSessionModel.authentication_method == "email_password",
                )
                .with_for_update()
            )
            if current_session is None:
                return False
            credential = await session.scalar(
                select(PasswordCredentialModel)
                .where(PasswordCredentialModel.user_id == current_session.user_id)
                .with_for_update()
            )
            if credential is None or credential.password_hash != expected_password_hash:
                return False
            credential.password_hash = new_password_hash
            credential.password_version += 1
            credential.changed_at = changed_at
            revoked = await session.execute(
                update(AdminSessionModel)
                .where(
                    AdminSessionModel.user_id == current_session.user_id,
                    AdminSessionModel.revoked_at.is_(None),
                )
                .values(revoked_at=changed_at)
            )
            session.add(
                PlatformAuditEventModel(
                    event_id=str(uuid4()),
                    event_type="email_password.changed",
                    actor_subject_id=current_session.user_id,
                    correlation_id=correlation_id,
                    resource_type="identity_user",
                    resource_id=current_session.user_id,
                    details={"revoked_session_count": revoked.rowcount or 0},
                    created_at=changed_at,
                )
            )
            return True

    async def bootstrap_email_identity(
        self,
        *,
        user_id: str,
        normalized_email: str,
        display_email: str,
        password_hash: str,
        occurred_at: datetime,
    ) -> EmailLoginAccount:
        async with self._session_factory.begin() as session:
            user = await session.scalar(
                select(IdentityUserModel)
                .where(IdentityUserModel.user_id == user_id)
                .with_for_update()
            )
            if user is None:
                raise LookupError("bootstrap identity user was not found")
            email_identity = await session.scalar(
                select(EmailIdentityModel)
                .where(EmailIdentityModel.normalized_email == normalized_email)
                .with_for_update()
            )
            if email_identity is not None and email_identity.user_id != user_id:
                raise ValueError("bootstrap email is already assigned to another user")
            if email_identity is None:
                email_identity = EmailIdentityModel(
                    identity_id=str(uuid4()),
                    user_id=user_id,
                    normalized_email=normalized_email,
                    display_email=display_email,
                    status="active",
                    verified_at=occurred_at,
                    created_at=occurred_at,
                    updated_at=occurred_at,
                )
                session.add(email_identity)
            credential = await session.scalar(
                select(PasswordCredentialModel)
                .where(PasswordCredentialModel.user_id == user_id)
                .with_for_update()
            )
            if credential is None:
                credential = PasswordCredentialModel(
                    user_id=user_id,
                    password_hash=password_hash,
                    password_version=1,
                    changed_at=occurred_at,
                    created_at=occurred_at,
                )
                session.add(credential)
            await session.flush()
            return EmailLoginAccount(
                user=IdentityUser(
                    user_id=user.user_id,
                    display_name=user.display_name,
                    status=user.status,
                    created_at=user.created_at,
                    updated_at=user.updated_at,
                    primary_email=email_identity.display_email,
                ),
                identity=_to_email_identity(email_identity),
                password_hash=credential.password_hash,
                password_version=credential.password_version,
            )


def _to_account(row) -> EmailLoginAccount | None:  # type: ignore[no-untyped-def]
    if row is None:
        return None
    user, identity, credential = row
    return EmailLoginAccount(
        user=IdentityUser(
            user_id=user.user_id,
            display_name=user.display_name,
            status=user.status,
            created_at=user.created_at,
            updated_at=user.updated_at,
            primary_email=identity.display_email,
        ),
        identity=_to_email_identity(identity),
        password_hash=credential.password_hash,
        password_version=credential.password_version,
    )


def _to_email_identity(model: EmailIdentityModel) -> EmailIdentity:
    return EmailIdentity(
        identity_id=model.identity_id,
        user_id=model.user_id,
        normalized_email=model.normalized_email,
        display_email=model.display_email,
        status=model.status,
        verified_at=model.verified_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _to_invitation(model: TenantInvitationModel, tenant_name: str) -> TenantInvitation:
    return TenantInvitation(
        invitation_id=model.invitation_id,
        tenant_id=model.tenant_id,
        tenant_name=tenant_name,
        normalized_email=model.normalized_email,
        display_email=model.display_email,
        roles=frozenset(model.roles),
        token_hash=model.token_hash,
        status=model.status,
        expires_at=model.expires_at,
        created_by=model.created_by,
        created_at=model.created_at,
        redeemed_by=model.redeemed_by,
        redeemed_at=model.redeemed_at,
        revoked_by=model.revoked_by,
        revoked_at=model.revoked_at,
    )


async def _set_transaction_tenant(session: AsyncSession, tenant_id: str) -> None:
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": tenant_id},
    )


def _add_session(session: AsyncSession, model: AdminSession) -> None:
    session.add(
        AdminSessionModel(
            tenant_id=model.tenant_id,
            session_id=model.session_id,
            user_id=model.user_id,
            token_hash=model.token_hash,
            expires_at=model.expires_at,
            created_at=model.created_at,
            last_seen_at=model.last_seen_at,
            source_fingerprint=model.source_fingerprint,
            authentication_method=model.authentication_method,
            revoked_at=model.revoked_at,
        )
    )
    session.add(
        AuditEventModel(
            tenant_id=model.tenant_id,
            event_id=str(uuid5(NAMESPACE_URL, f"admin-session:{model.session_id}:created")),
            event_type="admin_session.created",
            actor_subject_id=model.user_id,
            resource_type="admin_session",
            resource_id=model.session_id,
            details={
                "expires_at": model.expires_at.isoformat(),
                "authentication_method": model.authentication_method,
                "source_fingerprint_prefix": model.source_fingerprint[:12],
            },
            created_at=model.created_at,
        )
    )
