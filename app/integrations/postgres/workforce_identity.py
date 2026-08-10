from datetime import datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import and_, cast, exists, func, or_, select, text
from sqlalchemy.dialects.postgresql import JSONB, aggregate_order_by
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.models import (
    AdminSession,
    AdminUser,
    IdentityUser,
    LoginCompletionGrant,
    OAuthLoginState,
    PlatformActivity,
    PlatformMembershipRecord,
    PlatformOnboardingSourceRecord,
    PlatformSiteRecord,
    PlatformSummary,
    PlatformTenantRecord,
    PlatformUserRecord,
    Tenant,
    TenantMembership,
    VerifiedExternalIdentity,
)
from app.domain.rules.rbac import scopes_for_roles
from app.integrations.postgres.models import (
    AdminSessionModel,
    AdminUserModel,
    AuditEventModel,
    EmailIdentityModel,
    EnrollmentEmailDeliveryModel,
    EnrollmentIntentModel,
    ExternalIdentityModel,
    IdentityUserModel,
    LoginCompletionGrantModel,
    OAuthLoginStateModel,
    PlatformAuditEventModel,
    PlatformRoleAssignmentModel,
    PlatformSiteDirectoryModel,
    PlatformTenantEntitlementModel,
    SupportQueueMembershipModel,
    SupportQueueModel,
    TenantEnrollmentCodeModel,
    TenantMembershipModel,
    TenantModel,
    TenantSettingsModel,
)


class PostgreSQLWorkforceIdentityStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save_login_state(self, state: OAuthLoginState) -> None:
        async with self._session_factory.begin() as session:
            session.add(
                OAuthLoginStateModel(
                    state_id=state.state_id,
                    provider=state.provider,
                    state_hash=state.state_hash,
                    return_path=state.return_path,
                    expires_at=state.expires_at,
                    created_at=state.created_at,
                    consumed_at=state.consumed_at,
                )
            )

    async def consume_login_state(
        self,
        *,
        provider: str,
        state_hash: str,
        consumed_at: datetime,
    ) -> OAuthLoginState | None:
        async with self._session_factory.begin() as session:
            model = await session.scalar(
                select(OAuthLoginStateModel)
                .where(
                    OAuthLoginStateModel.provider == provider,
                    OAuthLoginStateModel.state_hash == state_hash,
                    OAuthLoginStateModel.consumed_at.is_(None),
                    OAuthLoginStateModel.expires_at > consumed_at,
                )
                .with_for_update()
            )
            if model is None:
                return None
            model.consumed_at = consumed_at
            return _to_login_state(model)

    async def upsert_external_user(
        self,
        *,
        identity: VerifiedExternalIdentity,
        correlation_id: str,
        occurred_at: datetime,
    ) -> IdentityUser:
        async with self._session_factory.begin() as session:
            model = await session.scalar(
                select(ExternalIdentityModel)
                .where(
                    ExternalIdentityModel.provider == identity.provider,
                    ExternalIdentityModel.organization_id == identity.organization_id,
                    ExternalIdentityModel.provider_subject_id == identity.provider_subject_id,
                )
                .with_for_update()
            )
            if model is None:
                user_id = str(
                    uuid5(
                        NAMESPACE_URL,
                        ":".join(
                            (
                                "external-user",
                                identity.provider,
                                identity.organization_id,
                                identity.provider_subject_id,
                            )
                        ),
                    )
                )
                user_model = await session.scalar(
                    select(IdentityUserModel)
                    .where(IdentityUserModel.user_id == user_id)
                    .with_for_update()
                )
                if user_model is None:
                    user_model = IdentityUserModel(
                        user_id=user_id,
                        display_name=identity.display_name,
                        status="active",
                        created_at=occurred_at,
                        updated_at=occurred_at,
                    )
                    session.add(user_model)
                model = ExternalIdentityModel(
                    identity_id=str(uuid4()),
                    user_id=user_id,
                    provider=identity.provider,
                    organization_id=identity.organization_id,
                    provider_subject_id=identity.provider_subject_id,
                    provider_user_id=identity.provider_user_id,
                    created_at=occurred_at,
                    updated_at=occurred_at,
                )
                session.add(model)
                session.add(
                    PlatformAuditEventModel(
                        event_id=str(uuid4()),
                        event_type="external_identity.bound",
                        actor_subject_id=user_id,
                        correlation_id=correlation_id,
                        resource_type="external_identity",
                        resource_id=model.identity_id,
                        details={
                            "provider": identity.provider,
                            "organization_id": identity.organization_id,
                        },
                        created_at=occurred_at,
                    )
                )
            else:
                user_model = await session.scalar(
                    select(IdentityUserModel)
                    .where(IdentityUserModel.user_id == model.user_id)
                    .with_for_update()
                )
                if user_model is None:
                    raise RuntimeError("external identity references a missing user")
                model.provider_user_id = identity.provider_user_id
                model.updated_at = occurred_at
                if identity.display_name and user_model.display_name != identity.display_name:
                    user_model.display_name = identity.display_name
                    user_model.updated_at = occurred_at
            return _to_identity_user(user_model)

    async def list_active_memberships(self, *, user_id: str) -> list[TenantMembership]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(TenantMembershipModel, TenantModel)
                    .join(TenantModel, TenantModel.tenant_id == TenantMembershipModel.tenant_id)
                    .where(
                        TenantMembershipModel.user_id == user_id,
                        TenantMembershipModel.status == "active",
                        TenantModel.status == "active",
                    )
                    .order_by(TenantModel.name, TenantModel.tenant_id)
                )
            ).all()
        return [_to_membership(membership, tenant) for membership, tenant in rows]

    async def get_workspace_user(self, *, user_id: str, tenant_id: str) -> AdminUser | None:
        async with self._session_factory() as session:
            row = await _workspace_user_row(session, user_id=user_id, tenant_id=tenant_id)
            platform_roles = await _platform_roles(session, user_id)
        return _workspace_user_from_row(
            row,
            authentication_method="external",
            platform_roles=platform_roles,
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

    async def consume_login_completion_grant(
        self,
        *,
        token_hash: str,
        consumed_at: datetime,
    ) -> LoginCompletionGrant | None:
        async with self._session_factory.begin() as session:
            model = await _lock_active_grant(session, token_hash=token_hash, checked_at=consumed_at)
            if model is None:
                return None
            model.consumed_at = consumed_at
            return _to_completion_grant(model)

    async def get_login_completion_grant(
        self,
        *,
        token_hash: str,
        checked_at: datetime,
    ) -> LoginCompletionGrant | None:
        async with self._session_factory() as session:
            model = await session.scalar(
                select(LoginCompletionGrantModel).where(
                    LoginCompletionGrantModel.token_hash == token_hash,
                    LoginCompletionGrantModel.consumed_at.is_(None),
                    LoginCompletionGrantModel.expires_at > checked_at,
                )
            )
        return _to_completion_grant(model) if model is not None else None

    async def create_session(self, session: AdminSession) -> None:
        workspace_user = await self.create_session_for_workspace(session)
        if workspace_user is None:
            raise PermissionError("workspace membership is invalid or inactive")

    async def create_session_for_workspace(self, session: AdminSession) -> AdminUser | None:
        async with self._session_factory.begin() as database_session:
            row = await _workspace_user_row(
                database_session,
                user_id=session.user_id,
                tenant_id=session.tenant_id,
                lock=True,
            )
            user = _workspace_user_from_row(
                row,
                authentication_method=session.authentication_method,
                platform_roles=await _platform_roles(database_session, session.user_id),
            )
            if user is None:
                return None
            await _set_transaction_tenant(database_session, session.tenant_id)
            _add_session(database_session, session)
            return user

    async def list_tenants(self) -> list[Tenant]:
        async with self._session_factory() as session:
            models = list(await session.scalars(select(TenantModel).order_by(TenantModel.name)))
        return [_to_tenant(model) for model in models]

    async def list_identity_users(self) -> list[IdentityUser]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(IdentityUserModel, EmailIdentityModel)
                    .outerjoin(
                        EmailIdentityModel,
                        EmailIdentityModel.user_id == IdentityUserModel.user_id,
                    )
                    .order_by(IdentityUserModel.display_name)
                )
            ).all()
        return [_to_identity_user(model, email_identity) for model, email_identity in rows]

    async def get_platform_summary(self, *, checked_at: datetime) -> PlatformSummary:
        async with self._session_factory() as session:
            active_workspaces = int(
                await session.scalar(
                    select(func.count())
                    .select_from(TenantModel)
                    .where(TenantModel.status == "active")
                )
                or 0
            )
            user_count = int(
                await session.scalar(
                    select(func.count())
                    .select_from(IdentityUserModel)
                    .where(IdentityUserModel.status == "active")
                )
                or 0
            )
            pending_onboarding = int(
                await session.scalar(
                    select(func.count())
                    .select_from(TenantEnrollmentCodeModel)
                    .where(
                        TenantEnrollmentCodeModel.status.in_(("issued", "reserved")),
                        TenantEnrollmentCodeModel.expires_at > checked_at,
                    )
                )
                or 0
            )
            failed_email_count = int(
                await session.scalar(
                    select(func.count())
                    .select_from(EnrollmentEmailDeliveryModel)
                    .where(EnrollmentEmailDeliveryModel.status == "failed")
                )
                or 0
            )
            expiring_code_count = int(
                await session.scalar(
                    select(func.count())
                    .select_from(TenantEnrollmentCodeModel)
                    .where(
                        TenantEnrollmentCodeModel.status.in_(("issued", "reserved")),
                        TenantEnrollmentCodeModel.expires_at > checked_at,
                        TenantEnrollmentCodeModel.expires_at <= checked_at + timedelta(hours=24),
                    )
                )
                or 0
            )
            active_owner = exists(
                select(1).where(
                    TenantMembershipModel.tenant_id == TenantModel.tenant_id,
                    TenantMembershipModel.status == "active",
                    cast(TenantMembershipModel.roles, JSONB).contains(["tenant_owner"]),
                )
            )
            orphan_workspace_count = int(
                await session.scalar(
                    select(func.count())
                    .select_from(TenantModel)
                    .where(TenantModel.status == "active", ~active_owner)
                )
                or 0
            )
            activity_models = list(
                await session.scalars(
                    select(PlatformAuditEventModel)
                    .order_by(PlatformAuditEventModel.created_at.desc())
                    .limit(8)
                )
            )
        return PlatformSummary(
            active_workspace_count=active_workspaces,
            user_count=user_count,
            pending_onboarding_count=pending_onboarding,
            attention_count=failed_email_count + expiring_code_count + orphan_workspace_count,
            expiring_code_count=expiring_code_count,
            failed_email_count=failed_email_count,
            orphan_workspace_count=orphan_workspace_count,
            recent_activity=tuple(
                PlatformActivity(
                    event_type=item.event_type,
                    actor_subject_id=item.actor_subject_id,
                    resource_type=item.resource_type,
                    resource_id=item.resource_id,
                    details=dict(item.details or {}),
                    created_at=item.created_at,
                )
                for item in activity_models
            ),
        )

    async def list_platform_tenant_records(
        self,
        *,
        search: str,
        status: str,
        limit: int,
        after_updated_at: datetime | None,
        after_tenant_id: str | None,
    ) -> tuple[list[PlatformTenantRecord], int, bool]:
        filters = []
        if search:
            pattern = f"%{search}%"
            filters.append(
                or_(TenantModel.name.ilike(pattern), TenantModel.tenant_id.ilike(pattern))
            )
        if status:
            filters.append(TenantModel.status == status)
        cursor_filter = None
        if after_updated_at is not None and after_tenant_id is not None:
            cursor_filter = or_(
                TenantModel.updated_at < after_updated_at,
                and_(
                    TenantModel.updated_at == after_updated_at,
                    TenantModel.tenant_id < after_tenant_id,
                ),
            )
        statement = _platform_tenant_record_statement().where(*filters)
        if cursor_filter is not None:
            statement = statement.where(cursor_filter)
        statement = statement.order_by(
            TenantModel.updated_at.desc(), TenantModel.tenant_id.desc()
        ).limit(limit + 1)
        async with self._session_factory() as session:
            total = int(
                await session.scalar(select(func.count()).select_from(TenantModel).where(*filters))
                or 0
            )
            rows = (await session.execute(statement)).all()
        has_more = len(rows) > limit
        return [_platform_tenant_record(row) for row in rows[:limit]], total, has_more

    async def get_platform_tenant_record(self, *, tenant_id: str) -> PlatformTenantRecord | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    _platform_tenant_record_statement().where(TenantModel.tenant_id == tenant_id)
                )
            ).first()
        return _platform_tenant_record(row) if row is not None else None

    async def list_platform_site_records(
        self,
        *,
        search: str,
        tenant_id: str | None,
        status: str,
        verification_status: str,
        include_disabled: bool,
        limit: int,
        after_tenant_id: str | None,
        after_site_id: str | None,
        checked_at: datetime,
    ) -> tuple[list[PlatformSiteRecord], int, bool]:
        filters = []
        if tenant_id:
            filters.append(PlatformSiteDirectoryModel.tenant_id == tenant_id)
        if search:
            pattern = f"%{search}%"
            filters.append(
                or_(
                    PlatformSiteDirectoryModel.name.ilike(pattern),
                    PlatformSiteDirectoryModel.site_id.ilike(pattern),
                    PlatformSiteDirectoryModel.base_url.ilike(pattern),
                    TenantModel.name.ilike(pattern),
                    TenantModel.tenant_id.ilike(pattern),
                )
            )
        if status:
            filters.append(PlatformSiteDirectoryModel.status == status)
        elif not include_disabled:
            filters.append(PlatformSiteDirectoryModel.status == "active")
        pending_challenge_expired = and_(
            PlatformSiteDirectoryModel.verification_status == "pending",
            PlatformSiteDirectoryModel.verification_expires_at.is_not(None),
            PlatformSiteDirectoryModel.verification_expires_at <= checked_at,
        )
        if verification_status == "expired":
            filters.append(
                or_(
                    PlatformSiteDirectoryModel.verification_status == "expired",
                    pending_challenge_expired,
                )
            )
        elif verification_status == "pending":
            filters.append(
                and_(
                    PlatformSiteDirectoryModel.verification_status == "pending",
                    or_(
                        PlatformSiteDirectoryModel.verification_expires_at.is_(None),
                        PlatformSiteDirectoryModel.verification_expires_at > checked_at,
                    ),
                )
            )
        elif verification_status:
            filters.append(PlatformSiteDirectoryModel.verification_status == verification_status)
        cursor_filter = None
        if after_tenant_id is not None and after_site_id is not None:
            cursor_filter = or_(
                PlatformSiteDirectoryModel.tenant_id > after_tenant_id,
                and_(
                    PlatformSiteDirectoryModel.tenant_id == after_tenant_id,
                    PlatformSiteDirectoryModel.site_id > after_site_id,
                ),
            )
        base = (
            select(PlatformSiteDirectoryModel, TenantModel)
            .join(TenantModel, TenantModel.tenant_id == PlatformSiteDirectoryModel.tenant_id)
            .where(*filters)
        )
        if cursor_filter is not None:
            base = base.where(cursor_filter)
        statement = base.order_by(
            PlatformSiteDirectoryModel.tenant_id.asc(),
            PlatformSiteDirectoryModel.site_id.asc(),
        ).limit(limit + 1)
        count_statement = (
            select(func.count())
            .select_from(PlatformSiteDirectoryModel)
            .join(TenantModel, TenantModel.tenant_id == PlatformSiteDirectoryModel.tenant_id)
            .where(*filters)
        )
        async with self._session_factory() as session:
            total = int(await session.scalar(count_statement) or 0)
            rows = (await session.execute(statement)).all()
            page_rows = rows[:limit]
            tenant_ids = {site.tenant_id for site, _tenant in page_rows}
            owner_rows = []
            if tenant_ids:
                owner_rows = (
                    await session.execute(
                        select(
                            TenantMembershipModel,
                            IdentityUserModel,
                            EmailIdentityModel,
                        )
                        .join(
                            IdentityUserModel,
                            IdentityUserModel.user_id == TenantMembershipModel.user_id,
                        )
                        .outerjoin(
                            EmailIdentityModel,
                            (EmailIdentityModel.user_id == IdentityUserModel.user_id)
                            & (EmailIdentityModel.status == "active"),
                        )
                        .where(
                            TenantMembershipModel.tenant_id.in_(tenant_ids),
                            TenantMembershipModel.status == "active",
                            IdentityUserModel.status == "active",
                            cast(TenantMembershipModel.roles, JSONB).contains(["tenant_owner"]),
                        )
                        .order_by(
                            TenantMembershipModel.tenant_id,
                            TenantMembershipModel.created_at,
                            IdentityUserModel.user_id,
                        )
                    )
                ).all()
        owners: dict[str, list[tuple[str, str | None]]] = {}
        for membership, user, email in owner_rows:
            owners.setdefault(membership.tenant_id, []).append(
                (user.display_name, email.display_email if email else None)
            )
        has_more = len(rows) > limit
        return (
            [
                _platform_site_record(site, tenant, owners.get(site.tenant_id, []))
                for site, tenant in page_rows
            ],
            total,
            has_more,
        )

    async def list_platform_memberships(self, *, tenant_id: str) -> list[PlatformMembershipRecord]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(TenantMembershipModel, IdentityUserModel, EmailIdentityModel)
                    .join(
                        IdentityUserModel,
                        IdentityUserModel.user_id == TenantMembershipModel.user_id,
                    )
                    .outerjoin(
                        EmailIdentityModel,
                        EmailIdentityModel.user_id == IdentityUserModel.user_id,
                    )
                    .where(TenantMembershipModel.tenant_id == tenant_id)
                    .order_by(IdentityUserModel.display_name, IdentityUserModel.user_id)
                )
            ).all()
        return [
            PlatformMembershipRecord(
                membership_id=membership.membership_id,
                tenant_id=membership.tenant_id,
                user_id=user.user_id,
                display_name=user.display_name,
                email=email.display_email if email is not None else None,
                roles=tuple(membership.roles),
                status=membership.status,
                created_at=membership.created_at,
                updated_at=membership.updated_at,
            )
            for membership, user, email in rows
        ]

    async def list_platform_user_records(
        self,
        *,
        search: str,
        status: str,
        limit: int,
        after_updated_at: datetime | None,
        after_user_id: str | None,
    ) -> tuple[list[PlatformUserRecord], int, bool]:
        filters = []
        if search:
            pattern = f"%{search}%"
            matching_email = exists(
                select(1).where(
                    EmailIdentityModel.user_id == IdentityUserModel.user_id,
                    EmailIdentityModel.display_email.ilike(pattern),
                )
            )
            filters.append(or_(IdentityUserModel.display_name.ilike(pattern), matching_email))
        if status:
            filters.append(IdentityUserModel.status == status)
        cursor_filter = None
        if after_updated_at is not None and after_user_id is not None:
            cursor_filter = or_(
                IdentityUserModel.updated_at < after_updated_at,
                and_(
                    IdentityUserModel.updated_at == after_updated_at,
                    IdentityUserModel.user_id < after_user_id,
                ),
            )
        statement = _platform_user_record_statement().where(*filters)
        if cursor_filter is not None:
            statement = statement.where(cursor_filter)
        statement = statement.order_by(
            IdentityUserModel.updated_at.desc(), IdentityUserModel.user_id.desc()
        ).limit(limit + 1)
        async with self._session_factory() as session:
            total = int(
                await session.scalar(
                    select(func.count()).select_from(IdentityUserModel).where(*filters)
                )
                or 0
            )
            rows = (await session.execute(statement)).all()
        has_more = len(rows) > limit
        return [_platform_user_record(row) for row in rows[:limit]], total, has_more

    async def list_platform_onboarding_records(
        self, *, search: str
    ) -> list[PlatformOnboardingSourceRecord]:
        latest_intent_id = (
            select(EnrollmentIntentModel.intent_id)
            .where(EnrollmentIntentModel.code_id == TenantEnrollmentCodeModel.code_id)
            .order_by(EnrollmentIntentModel.created_at.desc())
            .limit(1)
            .correlate(TenantEnrollmentCodeModel)
            .scalar_subquery()
        )
        latest_delivery_id = (
            select(EnrollmentEmailDeliveryModel.delivery_id)
            .where(EnrollmentEmailDeliveryModel.intent_id == latest_intent_id)
            .order_by(EnrollmentEmailDeliveryModel.created_at.desc())
            .limit(1)
            .correlate(TenantEnrollmentCodeModel)
            .scalar_subquery()
        )
        statement = (
            select(
                TenantEnrollmentCodeModel,
                EnrollmentIntentModel,
                EnrollmentEmailDeliveryModel,
                IdentityUserModel.display_name.label("created_by_name"),
            )
            .outerjoin(
                EnrollmentIntentModel,
                EnrollmentIntentModel.intent_id == latest_intent_id,
            )
            .outerjoin(
                EnrollmentEmailDeliveryModel,
                EnrollmentEmailDeliveryModel.delivery_id == latest_delivery_id,
            )
            .outerjoin(
                IdentityUserModel,
                IdentityUserModel.user_id == TenantEnrollmentCodeModel.created_by,
            )
        )
        if search:
            pattern = f"%{search}%"
            statement = statement.where(
                or_(
                    TenantEnrollmentCodeModel.target_email.ilike(pattern),
                    TenantEnrollmentCodeModel.code_id.ilike(pattern),
                    EnrollmentIntentModel.workspace_name.ilike(pattern),
                )
            )
        statement = statement.order_by(
            TenantEnrollmentCodeModel.created_at.desc(),
            TenantEnrollmentCodeModel.code_id.desc(),
        )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).all()
        return [
            PlatformOnboardingSourceRecord(
                code_id=code.code_id,
                policy_id=code.policy_id,
                target_email=code.target_email,
                code_status=code.status,
                expires_at=code.expires_at,
                created_by=code.created_by,
                created_by_name=created_by_name,
                created_at=code.created_at,
                intent_status=intent.status if intent is not None else None,
                workspace_name=intent.workspace_name if intent is not None else None,
                proposed_tenant_id=intent.proposed_tenant_id if intent is not None else None,
                intent_expires_at=intent.expires_at if intent is not None else None,
                completed_at=intent.completed_at if intent is not None else None,
                email_status=delivery.status if delivery is not None else None,
                email_attempts=delivery.attempts if delivery is not None else None,
                email_sent_at=delivery.sent_at if delivery is not None else None,
                email_last_error=delivery.last_error if delivery is not None else None,
            )
            for code, intent, delivery, created_by_name in rows
        ]

    async def provision_tenant(self, tenant: Tenant, *, actor_subject_id: str) -> Tenant:
        async with self._session_factory.begin() as session:
            await session.execute(
                pg_insert(TenantModel)
                .values(
                    tenant_id=tenant.tenant_id,
                    name=tenant.name,
                    status=tenant.status,
                    created_at=tenant.created_at,
                    updated_at=tenant.updated_at,
                )
                .on_conflict_do_nothing(index_elements=[TenantModel.tenant_id])
            )
            existing = await session.scalar(
                select(TenantModel).where(TenantModel.tenant_id == tenant.tenant_id)
            )
            if existing is None:
                raise RuntimeError("tenant provisioning did not persist the tenant")
            await _set_transaction_tenant(session, tenant.tenant_id)
            await session.execute(
                pg_insert(TenantSettingsModel)
                .values(
                    tenant_id=tenant.tenant_id,
                    primary_language="zh-CN",
                    timezone="Asia/Shanghai",
                    conversation_retention_days=180,
                    notification_settings={},
                    created_at=tenant.created_at,
                    updated_at=tenant.updated_at,
                )
                .on_conflict_do_nothing(index_elements=[TenantSettingsModel.tenant_id])
            )
            queue_definitions = (
                ("general", "通用客服", "默认客服队列", True),
                ("orders", "订单人工客服", "订单、物流、退款、取消、支付和地址问题", False),
            )
            for queue_id, name, description, is_default in queue_definitions:
                await session.execute(
                    pg_insert(SupportQueueModel)
                    .values(
                        tenant_id=tenant.tenant_id,
                        queue_id=queue_id,
                        name=name,
                        description=description,
                        is_default=is_default,
                        status="active",
                        created_at=tenant.created_at,
                        updated_at=tenant.updated_at,
                    )
                    .on_conflict_do_nothing(
                        index_elements=[SupportQueueModel.tenant_id, SupportQueueModel.queue_id]
                    )
                )
            await session.execute(
                pg_insert(PlatformAuditEventModel)
                .values(
                    event_id=str(
                        uuid5(NAMESPACE_URL, f"tenant-provisioning:{tenant.tenant_id}:v1")
                    ),
                    event_type="tenant.provisioned",
                    actor_subject_id=actor_subject_id,
                    correlation_id=None,
                    resource_type="tenant",
                    resource_id=tenant.tenant_id,
                    details={"name": tenant.name, "queue_ids": ["general", "orders"]},
                    created_at=tenant.created_at,
                )
                .on_conflict_do_nothing(index_elements=[PlatformAuditEventModel.event_id])
            )
            return _to_tenant(existing)

    async def upsert_membership(
        self,
        membership: TenantMembership,
        *,
        actor_subject_id: str,
        correlation_id: str,
    ) -> TenantMembership:
        async with self._session_factory.begin() as session:
            tenant = await session.scalar(
                select(TenantModel).where(TenantModel.tenant_id == membership.tenant_id)
            )
            user = await session.scalar(
                select(IdentityUserModel).where(IdentityUserModel.user_id == membership.user_id)
            )
            if tenant is None or user is None:
                raise LookupError("tenant or identity user was not found")
            model = await session.scalar(
                select(TenantMembershipModel)
                .where(
                    TenantMembershipModel.tenant_id == membership.tenant_id,
                    TenantMembershipModel.user_id == membership.user_id,
                )
                .with_for_update()
            )
            event_type = "tenant_membership.updated"
            if model is None:
                event_type = "tenant_membership.created"
                model = TenantMembershipModel(
                    membership_id=membership.membership_id,
                    tenant_id=membership.tenant_id,
                    user_id=membership.user_id,
                    roles=sorted(membership.roles),
                    scopes=sorted(membership.scopes),
                    status=membership.status,
                    created_at=membership.created_at,
                    updated_at=membership.updated_at,
                )
                session.add(model)
            else:
                model.roles = sorted(membership.roles)
                model.scopes = sorted(membership.scopes)
                model.status = membership.status
                model.updated_at = membership.updated_at
                if membership.status != "active":
                    sessions = list(
                        await session.scalars(
                            select(AdminSessionModel)
                            .where(
                                AdminSessionModel.tenant_id == membership.tenant_id,
                                AdminSessionModel.user_id == membership.user_id,
                                AdminSessionModel.revoked_at.is_(None),
                            )
                            .with_for_update()
                        )
                    )
                    for active_session in sessions:
                        active_session.revoked_at = membership.updated_at
            if membership.status == "active" and {
                "support:inbox:reply:self",
                "support:inbox:takeover",
                "support:inbox:manage",
            }.intersection(membership.scopes):
                general_queue = await session.scalar(
                    select(SupportQueueModel).where(
                        SupportQueueModel.tenant_id == membership.tenant_id,
                        SupportQueueModel.queue_id == "general",
                        SupportQueueModel.status == "active",
                    )
                )
                if general_queue is not None:
                    await session.execute(
                        pg_insert(SupportQueueMembershipModel)
                        .values(
                            tenant_id=membership.tenant_id,
                            queue_id="general",
                            user_id=membership.user_id,
                            role="member",
                            status="active",
                            created_at=membership.updated_at,
                            updated_at=membership.updated_at,
                        )
                        .on_conflict_do_update(
                            index_elements=[
                                SupportQueueMembershipModel.tenant_id,
                                SupportQueueMembershipModel.queue_id,
                                SupportQueueMembershipModel.user_id,
                            ],
                            set_={"status": "active", "updated_at": membership.updated_at},
                        )
                    )
            elif membership.status != "active":
                memberships = list(
                    await session.scalars(
                        select(SupportQueueMembershipModel)
                        .where(
                            SupportQueueMembershipModel.tenant_id == membership.tenant_id,
                            SupportQueueMembershipModel.user_id == membership.user_id,
                            SupportQueueMembershipModel.status == "active",
                        )
                        .with_for_update()
                    )
                )
                for queue_membership in memberships:
                    queue_membership.status = "disabled"
                    queue_membership.updated_at = membership.updated_at
            session.add(
                PlatformAuditEventModel(
                    event_id=str(uuid4()),
                    event_type=event_type,
                    actor_subject_id=actor_subject_id,
                    correlation_id=correlation_id,
                    resource_type="tenant_membership",
                    resource_id=membership.membership_id,
                    details={
                        "tenant_id": membership.tenant_id,
                        "user_id": membership.user_id,
                        "roles": sorted(membership.roles),
                        "status": membership.status,
                    },
                    created_at=membership.updated_at,
                )
            )
            return TenantMembership(
                membership_id=model.membership_id,
                tenant_id=model.tenant_id,
                tenant_name=tenant.name,
                user_id=model.user_id,
                roles=frozenset(model.roles),
                scopes=frozenset(model.scopes),
                status=model.status,
                created_at=model.created_at,
                updated_at=model.updated_at,
            )

    async def assign_platform_role(
        self,
        *,
        user_id: str,
        role: str,
        actor_subject_id: str,
        correlation_id: str,
        assigned_at: datetime,
    ) -> None:
        async with self._session_factory.begin() as session:
            user = await session.scalar(
                select(IdentityUserModel).where(IdentityUserModel.user_id == user_id)
            )
            if user is None:
                raise LookupError("identity user was not found")
            assignment = await session.scalar(
                select(PlatformRoleAssignmentModel)
                .where(
                    PlatformRoleAssignmentModel.user_id == user_id,
                    PlatformRoleAssignmentModel.role == role,
                )
                .with_for_update()
            )
            if assignment is None:
                session.add(
                    PlatformRoleAssignmentModel(
                        user_id=user_id,
                        role=role,
                        status="active",
                        created_at=assigned_at,
                        updated_at=assigned_at,
                    )
                )
                session.add(
                    PlatformAuditEventModel(
                        event_id=str(uuid4()),
                        event_type="platform_role.assigned",
                        actor_subject_id=actor_subject_id,
                        correlation_id=correlation_id,
                        resource_type="identity_user",
                        resource_id=user_id,
                        details={"role": role},
                        created_at=assigned_at,
                    )
                )
            elif assignment.status != "active":
                assignment.status = "active"
                assignment.updated_at = assigned_at
                session.add(
                    PlatformAuditEventModel(
                        event_id=str(uuid4()),
                        event_type="platform_role.assigned",
                        actor_subject_id=actor_subject_id,
                        correlation_id=correlation_id,
                        resource_type="identity_user",
                        resource_id=user_id,
                        details={"role": role, "reactivated": True},
                        created_at=assigned_at,
                    )
                )

    async def revoke_platform_role(
        self,
        *,
        user_id: str,
        role: str,
        actor_subject_id: str,
        correlation_id: str,
        revoked_at: datetime,
    ) -> None:
        async with self._session_factory.begin() as session:
            assignment = await session.scalar(
                select(PlatformRoleAssignmentModel)
                .where(
                    PlatformRoleAssignmentModel.user_id == user_id,
                    PlatformRoleAssignmentModel.role == role,
                )
                .with_for_update()
            )
            if assignment is None or assignment.status != "active":
                return
            if role == "platform_owner":
                owner_count = int(
                    await session.scalar(
                        select(func.count())
                        .select_from(PlatformRoleAssignmentModel)
                        .where(
                            PlatformRoleAssignmentModel.role == "platform_owner",
                            PlatformRoleAssignmentModel.status == "active",
                        )
                    )
                    or 0
                )
                if owner_count <= 1:
                    raise ValueError("the last active platform owner cannot be removed")
            assignment.status = "disabled"
            assignment.updated_at = revoked_at
            sessions = list(
                await session.scalars(
                    select(AdminSessionModel).where(
                        AdminSessionModel.user_id == user_id,
                        AdminSessionModel.revoked_at.is_(None),
                    )
                )
            )
            for active_session in sessions:
                active_session.revoked_at = revoked_at
            session.add(
                PlatformAuditEventModel(
                    event_id=str(uuid4()),
                    event_type="platform_role.revoked",
                    actor_subject_id=actor_subject_id,
                    correlation_id=correlation_id,
                    resource_type="identity_user",
                    resource_id=user_id,
                    details={"role": role},
                    created_at=revoked_at,
                )
            )

    async def create_session_from_login_grant(
        self,
        *,
        grant_token_hash: str,
        tenant_id: str,
        session: AdminSession,
        consumed_at: datetime,
    ) -> AdminUser | None:
        async with self._session_factory.begin() as database_session:
            grant = await _lock_active_grant(
                database_session,
                token_hash=grant_token_hash,
                checked_at=consumed_at,
            )
            if grant is None or grant.user_id != session.user_id:
                return None
            row = await _workspace_user_row(
                database_session,
                user_id=grant.user_id,
                tenant_id=tenant_id,
                lock=True,
            )
            user = _workspace_user_from_row(
                row,
                authentication_method=session.authentication_method,
                platform_roles=await _platform_roles(database_session, session.user_id),
            )
            if user is None:
                return None
            grant.consumed_at = consumed_at
            await _set_transaction_tenant(database_session, tenant_id)
            _add_session(database_session, session)
            return user

    async def rotate_session_workspace(
        self,
        *,
        current_token_hash: str,
        tenant_id: str,
        replacement: AdminSession,
        rotated_at: datetime,
    ) -> AdminUser | None:
        async with self._session_factory.begin() as database_session:
            current = await database_session.scalar(
                select(AdminSessionModel)
                .where(
                    AdminSessionModel.token_hash == current_token_hash,
                    AdminSessionModel.revoked_at.is_(None),
                    AdminSessionModel.expires_at > rotated_at,
                )
                .with_for_update()
            )
            if current is None or current.user_id != replacement.user_id:
                return None
            row = await _workspace_user_row(
                database_session,
                user_id=current.user_id,
                tenant_id=tenant_id,
                lock=True,
            )
            user = _workspace_user_from_row(
                row,
                authentication_method=current.authentication_method,
                platform_roles=await _platform_roles(database_session, current.user_id),
            )
            if user is None:
                return None
            current.revoked_at = rotated_at
            await _set_transaction_tenant(database_session, tenant_id)
            _add_session(database_session, replacement)
            database_session.add(
                PlatformAuditEventModel(
                    event_id=str(uuid4()),
                    event_type="workspace.switched",
                    actor_subject_id=current.user_id,
                    correlation_id=None,
                    resource_type="tenant",
                    resource_id=tenant_id,
                    details={"previous_tenant_id": current.tenant_id},
                    created_at=rotated_at,
                )
            )
            return user


def _platform_tenant_record_statement():  # type: ignore[no-untyped-def]
    owner_names = (
        select(
            func.array_agg(
                aggregate_order_by(
                    IdentityUserModel.display_name,
                    TenantMembershipModel.created_at.asc(),
                    IdentityUserModel.user_id.asc(),
                )
            )
        )
        .select_from(TenantMembershipModel)
        .join(
            IdentityUserModel,
            IdentityUserModel.user_id == TenantMembershipModel.user_id,
        )
        .where(
            TenantMembershipModel.tenant_id == TenantModel.tenant_id,
            TenantMembershipModel.status == "active",
            IdentityUserModel.status == "active",
            cast(TenantMembershipModel.roles, JSONB).contains(["tenant_owner"]),
        )
        .correlate(TenantModel)
        .scalar_subquery()
    )
    owner_emails = (
        select(
            func.array_agg(
                aggregate_order_by(
                    EmailIdentityModel.display_email,
                    TenantMembershipModel.created_at.asc(),
                    IdentityUserModel.user_id.asc(),
                )
            )
        )
        .select_from(TenantMembershipModel)
        .join(
            IdentityUserModel,
            IdentityUserModel.user_id == TenantMembershipModel.user_id,
        )
        .join(
            EmailIdentityModel,
            EmailIdentityModel.user_id == IdentityUserModel.user_id,
        )
        .where(
            TenantMembershipModel.tenant_id == TenantModel.tenant_id,
            TenantMembershipModel.status == "active",
            IdentityUserModel.status == "active",
            EmailIdentityModel.status == "active",
            cast(TenantMembershipModel.roles, JSONB).contains(["tenant_owner"]),
        )
        .correlate(TenantModel)
        .scalar_subquery()
    )
    member_count = (
        select(func.count())
        .select_from(TenantMembershipModel)
        .where(
            TenantMembershipModel.tenant_id == TenantModel.tenant_id,
            TenantMembershipModel.status == "active",
        )
        .correlate(TenantModel)
        .scalar_subquery()
    )
    disabled_member_count = (
        select(func.count())
        .select_from(TenantMembershipModel)
        .where(
            TenantMembershipModel.tenant_id == TenantModel.tenant_id,
            TenantMembershipModel.status == "disabled",
        )
        .correlate(TenantModel)
        .scalar_subquery()
    )
    site_count = (
        select(func.count())
        .select_from(PlatformSiteDirectoryModel)
        .where(
            PlatformSiteDirectoryModel.tenant_id == TenantModel.tenant_id,
            PlatformSiteDirectoryModel.status == "active",
        )
        .correlate(TenantModel)
        .scalar_subquery()
    )
    disabled_site_count = (
        select(func.count())
        .select_from(PlatformSiteDirectoryModel)
        .where(
            PlatformSiteDirectoryModel.tenant_id == TenantModel.tenant_id,
            PlatformSiteDirectoryModel.status == "disabled",
        )
        .correlate(TenantModel)
        .scalar_subquery()
    )
    unverified_site_count = (
        select(func.count())
        .select_from(PlatformSiteDirectoryModel)
        .where(
            PlatformSiteDirectoryModel.tenant_id == TenantModel.tenant_id,
            PlatformSiteDirectoryModel.verification_status != "verified",
        )
        .correlate(TenantModel)
        .scalar_subquery()
    )
    site_limit = (
        select(PlatformTenantEntitlementModel.site_limit)
        .where(PlatformTenantEntitlementModel.tenant_id == TenantModel.tenant_id)
        .correlate(TenantModel)
        .scalar_subquery()
    )
    plan_id = (
        select(PlatformTenantEntitlementModel.plan_id)
        .where(PlatformTenantEntitlementModel.tenant_id == TenantModel.tenant_id)
        .correlate(TenantModel)
        .scalar_subquery()
    )
    subscription_status = (
        select(PlatformTenantEntitlementModel.subscription_status)
        .where(PlatformTenantEntitlementModel.tenant_id == TenantModel.tenant_id)
        .correlate(TenantModel)
        .scalar_subquery()
    )
    last_activity_at = (
        select(func.max(AdminSessionModel.last_seen_at))
        .where(AdminSessionModel.tenant_id == TenantModel.tenant_id)
        .correlate(TenantModel)
        .scalar_subquery()
    )
    return select(
        TenantModel,
        owner_names.label("owner_names"),
        owner_emails.label("owner_emails"),
        member_count.label("member_count"),
        disabled_member_count.label("disabled_member_count"),
        site_count.label("site_count"),
        disabled_site_count.label("disabled_site_count"),
        unverified_site_count.label("unverified_site_count"),
        site_limit.label("site_limit"),
        plan_id.label("plan_id"),
        subscription_status.label("subscription_status"),
        last_activity_at.label("last_activity_at"),
    )


def _platform_tenant_record(row) -> PlatformTenantRecord:  # type: ignore[no-untyped-def]
    tenant = row[0]
    owner_names = tuple(row.owner_names or ())
    owner_emails = tuple(row.owner_emails or ())
    return PlatformTenantRecord(
        tenant_id=tenant.tenant_id,
        name=tenant.name,
        status=tenant.status,
        owner_email=owner_emails[0] if owner_emails else None,
        member_count=int(row.member_count or 0),
        disabled_member_count=int(row.disabled_member_count or 0),
        site_count=int(row.site_count or 0),
        site_limit=row.site_limit,
        plan_id=row.plan_id,
        subscription_status=row.subscription_status,
        created_at=tenant.created_at,
        updated_at=tenant.updated_at,
        last_activity_at=row.last_activity_at,
        disabled_site_count=int(row.disabled_site_count or 0),
        unverified_site_count=int(row.unverified_site_count or 0),
        site_quota_used=int(row.site_count or 0) + int(row.disabled_site_count or 0),
        owner_names=owner_names,
        owner_emails=owner_emails,
    )


def _platform_site_record(
    site: PlatformSiteDirectoryModel,
    tenant: TenantModel,
    owners: list[tuple[str, str | None]],
) -> PlatformSiteRecord:
    return PlatformSiteRecord(
        tenant_id=site.tenant_id,
        tenant_name=tenant.name,
        site_id=site.site_id,
        name=site.name,
        base_url=site.base_url,
        status=site.status,
        verification_status=site.verification_status,
        knowledge_publication_state=site.knowledge_publication_state,
        manager_names=tuple(item[0] for item in owners),
        manager_emails=tuple(item[1] for item in owners if item[1]),
        created_at=site.created_at,
        updated_at=site.updated_at,
        verification_expires_at=site.verification_expires_at,
    )


def _platform_user_record_statement():  # type: ignore[no-untyped-def]
    email = (
        select(EmailIdentityModel.display_email)
        .where(EmailIdentityModel.user_id == IdentityUserModel.user_id)
        .correlate(IdentityUserModel)
        .scalar_subquery()
    )
    workspace_count = (
        select(func.count())
        .select_from(TenantMembershipModel)
        .where(
            TenantMembershipModel.user_id == IdentityUserModel.user_id,
            TenantMembershipModel.status == "active",
        )
        .correlate(IdentityUserModel)
        .scalar_subquery()
    )
    workspace_names = (
        select(func.array_agg(TenantModel.name))
        .select_from(TenantMembershipModel)
        .join(TenantModel, TenantModel.tenant_id == TenantMembershipModel.tenant_id)
        .where(
            TenantMembershipModel.user_id == IdentityUserModel.user_id,
            TenantMembershipModel.status == "active",
        )
        .correlate(IdentityUserModel)
        .scalar_subquery()
    )
    disabled_workspace_count = (
        select(func.count())
        .select_from(TenantMembershipModel)
        .where(
            TenantMembershipModel.user_id == IdentityUserModel.user_id,
            TenantMembershipModel.status == "disabled",
        )
        .correlate(IdentityUserModel)
        .scalar_subquery()
    )
    disabled_workspace_names = (
        select(func.array_agg(TenantModel.name))
        .select_from(TenantMembershipModel)
        .join(TenantModel, TenantModel.tenant_id == TenantMembershipModel.tenant_id)
        .where(
            TenantMembershipModel.user_id == IdentityUserModel.user_id,
            TenantMembershipModel.status == "disabled",
        )
        .correlate(IdentityUserModel)
        .scalar_subquery()
    )
    platform_roles = (
        select(func.array_agg(PlatformRoleAssignmentModel.role))
        .where(
            PlatformRoleAssignmentModel.user_id == IdentityUserModel.user_id,
            PlatformRoleAssignmentModel.status == "active",
        )
        .correlate(IdentityUserModel)
        .scalar_subquery()
    )
    last_login_at = (
        select(func.max(AdminSessionModel.last_seen_at))
        .where(AdminSessionModel.user_id == IdentityUserModel.user_id)
        .correlate(IdentityUserModel)
        .scalar_subquery()
    )
    return select(
        IdentityUserModel,
        email.label("email"),
        workspace_count.label("workspace_count"),
        workspace_names.label("workspace_names"),
        disabled_workspace_count.label("disabled_workspace_count"),
        disabled_workspace_names.label("disabled_workspace_names"),
        platform_roles.label("platform_roles"),
        last_login_at.label("last_login_at"),
    )


def _platform_user_record(row) -> PlatformUserRecord:  # type: ignore[no-untyped-def]
    user = row[0]
    return PlatformUserRecord(
        user_id=user.user_id,
        display_name=user.display_name,
        email=row.email,
        status=user.status,
        workspace_count=int(row.workspace_count or 0),
        workspace_names=tuple(row.workspace_names or ()),
        disabled_workspace_count=int(row.disabled_workspace_count or 0),
        disabled_workspace_names=tuple(row.disabled_workspace_names or ()),
        platform_roles=tuple(row.platform_roles or ()),
        last_login_at=row.last_login_at,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


async def _workspace_user_row(
    session: AsyncSession,
    *,
    user_id: str,
    tenant_id: str,
    lock: bool = False,
):  # type: ignore[no-untyped-def]
    statement = (
        select(IdentityUserModel, TenantMembershipModel, TenantModel, AdminUserModel)
        .join(
            TenantMembershipModel,
            TenantMembershipModel.user_id == IdentityUserModel.user_id,
        )
        .join(TenantModel, TenantModel.tenant_id == TenantMembershipModel.tenant_id)
        .outerjoin(
            AdminUserModel,
            (AdminUserModel.tenant_id == TenantMembershipModel.tenant_id)
            & (AdminUserModel.global_user_id == IdentityUserModel.user_id),
        )
        .where(
            IdentityUserModel.user_id == user_id,
            IdentityUserModel.status == "active",
            TenantMembershipModel.tenant_id == tenant_id,
            TenantMembershipModel.status == "active",
            TenantModel.status == "active",
        )
    )
    if lock:
        statement = statement.with_for_update(of=(IdentityUserModel, TenantMembershipModel))
    return (await session.execute(statement)).first()


def _workspace_user_from_row(  # type: ignore[no-untyped-def]
    row,
    *,
    authentication_method: str,
    platform_roles: frozenset[str] = frozenset(),
) -> AdminUser | None:
    if row is None:
        return None
    user, membership, _tenant, credential = row
    roles = frozenset(membership.roles)
    return AdminUser(
        user_id=user.user_id,
        tenant_id=membership.tenant_id,
        username=credential.username if credential is not None else user.user_id,
        display_name=user.display_name,
        password_hash=credential.password_hash if credential is not None else "",
        roles=roles,
        scopes=scopes_for_roles(roles),
        status=membership.status,
        created_at=user.created_at,
        updated_at=max(user.updated_at, membership.updated_at),
        authentication_method=authentication_method,
        platform_roles=platform_roles,
    )


async def _platform_roles(session: AsyncSession, user_id: str) -> frozenset[str]:
    return frozenset(
        await session.scalars(
            select(PlatformRoleAssignmentModel.role).where(
                PlatformRoleAssignmentModel.user_id == user_id,
                PlatformRoleAssignmentModel.status == "active",
            )
        )
    )


async def _lock_active_grant(
    session: AsyncSession,
    *,
    token_hash: str,
    checked_at: datetime,
) -> LoginCompletionGrantModel | None:
    return await session.scalar(
        select(LoginCompletionGrantModel)
        .where(
            LoginCompletionGrantModel.token_hash == token_hash,
            LoginCompletionGrantModel.consumed_at.is_(None),
            LoginCompletionGrantModel.expires_at > checked_at,
        )
        .with_for_update()
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


async def _set_transaction_tenant(session: AsyncSession, tenant_id: str) -> None:
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": tenant_id},
    )


def _to_identity_user(
    model: IdentityUserModel,
    email_identity: EmailIdentityModel | None = None,
) -> IdentityUser:
    return IdentityUser(
        user_id=model.user_id,
        display_name=model.display_name,
        status=model.status,
        created_at=model.created_at,
        updated_at=model.updated_at,
        primary_email=(email_identity.display_email if email_identity is not None else None),
    )


def _to_tenant(model: TenantModel) -> Tenant:
    return Tenant(
        tenant_id=model.tenant_id,
        name=model.name,
        status=model.status,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _to_membership(
    model: TenantMembershipModel,
    tenant: TenantModel,
) -> TenantMembership:
    roles = frozenset(model.roles)
    return TenantMembership(
        membership_id=model.membership_id,
        tenant_id=model.tenant_id,
        tenant_name=tenant.name,
        user_id=model.user_id,
        roles=roles,
        scopes=scopes_for_roles(roles),
        status=model.status,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _to_login_state(model: OAuthLoginStateModel) -> OAuthLoginState:
    return OAuthLoginState(
        state_id=model.state_id,
        provider=model.provider,
        state_hash=model.state_hash,
        return_path=model.return_path,
        expires_at=model.expires_at,
        created_at=model.created_at,
        consumed_at=model.consumed_at,
        authentication_method=model.authentication_method,
    )


def _to_completion_grant(model: LoginCompletionGrantModel) -> LoginCompletionGrant:
    return LoginCompletionGrant(
        grant_id=model.grant_id,
        user_id=model.user_id,
        token_hash=model.token_hash,
        expires_at=model.expires_at,
        created_at=model.created_at,
        consumed_at=model.consumed_at,
    )
