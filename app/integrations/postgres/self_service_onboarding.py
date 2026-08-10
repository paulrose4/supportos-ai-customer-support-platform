from datetime import datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.models.onboarding import (
    EmailVerificationToken,
    EnrollmentAuthority,
    EnrollmentCompletion,
    EnrollmentEmailDelivery,
    EnrollmentIntent,
    TenantEnrollmentCode,
    TenantEnrollmentPolicy,
)
from app.domain.rules.rbac import scopes_for_roles
from app.integrations.postgres.models import (
    AuditEventModel,
    EmailIdentityModel,
    EmailVerificationTokenModel,
    EnrollmentAuthorityModel,
    EnrollmentEmailDeliveryModel,
    EnrollmentIntentModel,
    IdentityUserModel,
    PasswordCredentialModel,
    PlatformAuditEventModel,
    SupportQueueModel,
    TenantEnrollmentCodeModel,
    TenantEnrollmentPolicyModel,
    TenantMembershipModel,
    TenantModel,
    TenantProvisioningModel,
    TenantQuotaModel,
    TenantSettingsModel,
    TenantSubscriptionModel,
)

_OPEN_INTENT_STATUSES = ("created", "verification_sent", "email_verified")


class PostgreSQLSelfServiceOnboardingStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_authority(
        self,
        authority: EnrollmentAuthority,
        *,
        correlation_id: str,
    ) -> EnrollmentAuthority:
        async with self._session_factory.begin() as session:
            existing = await session.scalar(
                select(EnrollmentAuthorityModel)
                .where(EnrollmentAuthorityModel.authority_id == authority.authority_id)
                .with_for_update()
            )
            if existing is not None:
                if (
                    existing.name != authority.name
                    or existing.max_active_tenants != authority.max_active_tenants
                    or existing.max_total_tenants != authority.max_total_tenants
                ):
                    raise ValueError("enrollment authority already exists with different settings")
                return _to_authority(existing)
            session.add(
                EnrollmentAuthorityModel(
                    authority_id=authority.authority_id,
                    name=authority.name,
                    status=authority.status,
                    max_active_tenants=authority.max_active_tenants,
                    max_total_tenants=authority.max_total_tenants,
                    active_tenant_count=authority.active_tenant_count,
                    reserved_tenant_count=authority.reserved_tenant_count,
                    total_tenant_count=authority.total_tenant_count,
                    created_by=authority.created_by,
                    created_at=authority.created_at,
                    updated_at=authority.updated_at,
                )
            )
            session.add(
                _platform_audit(
                    event_type="enrollment_authority.created",
                    actor_subject_id=authority.created_by,
                    correlation_id=correlation_id,
                    resource_type="enrollment_authority",
                    resource_id=authority.authority_id,
                    details={"max_active_tenants": authority.max_active_tenants},
                    created_at=authority.created_at,
                )
            )
            return authority

    async def create_policy(
        self,
        policy: TenantEnrollmentPolicy,
        *,
        correlation_id: str,
    ) -> TenantEnrollmentPolicy:
        async with self._session_factory.begin() as session:
            authority = await session.scalar(
                select(EnrollmentAuthorityModel)
                .where(EnrollmentAuthorityModel.authority_id == policy.authority_id)
                .with_for_update()
            )
            if authority is None or authority.status != "active":
                raise LookupError("enrollment authority was not found or is inactive")
            existing = await session.scalar(
                select(TenantEnrollmentPolicyModel)
                .where(TenantEnrollmentPolicyModel.policy_id == policy.policy_id)
                .with_for_update()
            )
            if existing is not None:
                if existing.authority_id != policy.authority_id or existing.name != policy.name:
                    raise ValueError("enrollment policy already exists with different settings")
                return _to_policy(existing)
            session.add(
                TenantEnrollmentPolicyModel(
                    policy_id=policy.policy_id,
                    authority_id=policy.authority_id,
                    name=policy.name,
                    enabled=policy.enabled,
                    allowed_email_domains=list(policy.allowed_email_domains),
                    require_exact_email_binding=policy.require_exact_email_binding,
                    default_plan_id=policy.default_plan_id,
                    default_role="tenant_owner",
                    require_email_verification=policy.require_email_verification,
                    require_mfa=policy.require_mfa,
                    site_limit=policy.site_limit,
                    created_by=policy.created_by,
                    created_at=policy.created_at,
                    updated_at=policy.updated_at,
                )
            )
            session.add(
                _platform_audit(
                    event_type="enrollment_policy.created",
                    actor_subject_id=policy.created_by,
                    correlation_id=correlation_id,
                    resource_type="enrollment_policy",
                    resource_id=policy.policy_id,
                    details={"authority_id": policy.authority_id, "enabled": policy.enabled},
                    created_at=policy.created_at,
                )
            )
            return policy

    async def ensure_authority_and_policy(
        self,
        authority: EnrollmentAuthority,
        policy: TenantEnrollmentPolicy,
        *,
        correlation_id: str,
    ) -> TenantEnrollmentPolicy:
        async with self._session_factory.begin() as session:
            authority_model = await session.scalar(
                select(EnrollmentAuthorityModel)
                .where(EnrollmentAuthorityModel.authority_id == authority.authority_id)
                .with_for_update()
            )
            if authority_model is None:
                authority_model = EnrollmentAuthorityModel(
                    authority_id=authority.authority_id,
                    name=authority.name,
                    status=authority.status,
                    max_active_tenants=authority.max_active_tenants,
                    max_total_tenants=authority.max_total_tenants,
                    active_tenant_count=authority.active_tenant_count,
                    reserved_tenant_count=authority.reserved_tenant_count,
                    total_tenant_count=authority.total_tenant_count,
                    created_by=authority.created_by,
                    created_at=authority.created_at,
                    updated_at=authority.updated_at,
                )
                session.add(authority_model)
                session.add(
                    _platform_audit(
                        event_type="enrollment_authority.created",
                        actor_subject_id=authority.created_by,
                        correlation_id=correlation_id,
                        resource_type="enrollment_authority",
                        resource_id=authority.authority_id,
                        details={"max_active_tenants": authority.max_active_tenants},
                        created_at=authority.created_at,
                    )
                )
            elif (
                authority_model.name != authority.name
                or authority_model.status != "active"
                or authority_model.max_active_tenants != authority.max_active_tenants
                or authority_model.max_total_tenants != authority.max_total_tenants
            ):
                raise ValueError("default enrollment authority has different settings")

            policy_model = await session.scalar(
                select(TenantEnrollmentPolicyModel)
                .where(TenantEnrollmentPolicyModel.policy_id == policy.policy_id)
                .with_for_update()
            )
            if policy_model is None:
                policy_model = TenantEnrollmentPolicyModel(
                    policy_id=policy.policy_id,
                    authority_id=policy.authority_id,
                    name=policy.name,
                    enabled=policy.enabled,
                    allowed_email_domains=list(policy.allowed_email_domains),
                    require_exact_email_binding=policy.require_exact_email_binding,
                    default_plan_id=policy.default_plan_id,
                    default_role="tenant_owner",
                    require_email_verification=policy.require_email_verification,
                    require_mfa=policy.require_mfa,
                    site_limit=policy.site_limit,
                    created_by=policy.created_by,
                    created_at=policy.created_at,
                    updated_at=policy.updated_at,
                )
                session.add(policy_model)
                session.add(
                    _platform_audit(
                        event_type="enrollment_policy.created",
                        actor_subject_id=policy.created_by,
                        correlation_id=correlation_id,
                        resource_type="enrollment_policy",
                        resource_id=policy.policy_id,
                        details={"authority_id": policy.authority_id, "enabled": policy.enabled},
                        created_at=policy.created_at,
                    )
                )
            elif (
                policy_model.authority_id != policy.authority_id
                or policy_model.name != policy.name
                or policy_model.allowed_email_domains != list(policy.allowed_email_domains)
                or policy_model.site_limit != policy.site_limit
                or not policy_model.enabled
            ):
                raise ValueError("default enrollment policy has different settings")
            return _to_policy(policy_model)

    async def create_code(
        self,
        code: TenantEnrollmentCode,
        *,
        correlation_id: str,
    ) -> TenantEnrollmentCode:
        async with self._session_factory.begin() as session:
            policy = await session.scalar(
                select(TenantEnrollmentPolicyModel)
                .where(TenantEnrollmentPolicyModel.policy_id == code.policy_id)
                .with_for_update()
            )
            if policy is None or not policy.enabled:
                raise LookupError("enrollment policy was not found or is disabled")
            model = TenantEnrollmentCodeModel(
                code_id=code.code_id,
                policy_id=code.policy_id,
                code_digest=code.code_digest,
                code_key_version=code.code_key_version,
                target_email=code.target_email,
                code_prefix=code.code_prefix,
                status=code.status,
                expires_at=code.expires_at,
                created_by=code.created_by,
                created_at=code.created_at,
            )
            session.add(model)
            try:
                await session.flush()
            except IntegrityError as exc:
                raise ValueError("enrollment code already exists") from exc
            session.add(
                _platform_audit(
                    event_type="enrollment_code.created",
                    actor_subject_id=code.created_by,
                    correlation_id=correlation_id,
                    resource_type="enrollment_code",
                    resource_id=code.code_id,
                    details={
                        "policy_id": code.policy_id,
                        "target_email_hash": _audit_hash(code.target_email),
                        "expires_at": code.expires_at.isoformat(),
                    },
                    created_at=code.created_at,
                )
            )
            return code

    async def get_code_by_digest(
        self,
        *,
        code_digest: str,
        checked_at: datetime,
    ) -> TenantEnrollmentCode | None:
        async with self._session_factory() as session:
            model = await session.scalar(
                select(TenantEnrollmentCodeModel).where(
                    TenantEnrollmentCodeModel.code_digest == code_digest,
                    TenantEnrollmentCodeModel.expires_at > checked_at,
                )
            )
        return _to_code(model) if model is not None else None

    async def get_policy(self, *, policy_id: str) -> TenantEnrollmentPolicy | None:
        async with self._session_factory() as session:
            model = await session.scalar(
                select(TenantEnrollmentPolicyModel).where(
                    TenantEnrollmentPolicyModel.policy_id == policy_id
                )
            )
        return _to_policy(model) if model is not None else None

    async def list_codes(self, *, policy_id: str) -> list[TenantEnrollmentCode]:
        async with self._session_factory() as session:
            models = list(
                await session.scalars(
                    select(TenantEnrollmentCodeModel)
                    .where(TenantEnrollmentCodeModel.policy_id == policy_id)
                    .order_by(TenantEnrollmentCodeModel.created_at.desc())
                )
            )
        return [_to_code(model) for model in models]

    async def expire_enrollment_intents(self, *, expired_at: datetime) -> int:
        released = 0
        async with self._session_factory.begin() as session:
            intents = list(
                await session.scalars(
                    select(EnrollmentIntentModel)
                    .where(
                        EnrollmentIntentModel.status.in_(_OPEN_INTENT_STATUSES),
                        EnrollmentIntentModel.expires_at <= expired_at,
                    )
                    .with_for_update(skip_locked=True)
                )
            )
            for intent in intents:
                intent.status = "expired"
                code = await session.scalar(
                    select(TenantEnrollmentCodeModel)
                    .where(TenantEnrollmentCodeModel.code_id == intent.code_id)
                    .with_for_update()
                )
                if code is not None and code.reserved_by_intent_id == intent.intent_id:
                    code.status = "expired" if code.expires_at <= expired_at else "issued"
                    code.reserved_by_intent_id = None
                    code.reserved_until = None
                    authority = await _authority_for_policy(session, intent.policy_id, lock=True)
                    if authority is not None and authority.reserved_tenant_count > 0:
                        authority.reserved_tenant_count -= 1
                    released += 1
                await session.execute(
                    update(EmailVerificationTokenModel)
                    .where(
                        EmailVerificationTokenModel.intent_id == intent.intent_id,
                        EmailVerificationTokenModel.consumed_at.is_(None),
                        EmailVerificationTokenModel.revoked_at.is_(None),
                    )
                    .values(revoked_at=expired_at)
                )
                await session.execute(
                    update(EnrollmentEmailDeliveryModel)
                    .where(
                        EnrollmentEmailDeliveryModel.intent_id == intent.intent_id,
                        EnrollmentEmailDeliveryModel.status.in_(
                            ("pending", "processing", "failed")
                        ),
                    )
                    .values(status="cancelled")
                )
                session.add(
                    _platform_audit(
                        event_type="enrollment.expired",
                        actor_subject_id=None,
                        correlation_id=None,
                        resource_type="enrollment_intent",
                        resource_id=intent.intent_id,
                        details={"code_id": intent.code_id},
                        created_at=expired_at,
                    )
                )
            return released

    async def revoke_code(
        self,
        *,
        code_id: str,
        actor_subject_id: str,
        correlation_id: str,
        revoked_at: datetime,
    ) -> TenantEnrollmentCode | None:
        async with self._session_factory.begin() as session:
            code = await session.scalar(
                select(TenantEnrollmentCodeModel)
                .where(TenantEnrollmentCodeModel.code_id == code_id)
                .with_for_update()
            )
            if code is None:
                return None
            if code.status not in {"consumed", "revoked", "expired"}:
                code.status = "revoked"
                code.revoked_by = actor_subject_id
                code.revoked_at = revoked_at
                if code.reserved_by_intent_id:
                    intent = await session.scalar(
                        select(EnrollmentIntentModel)
                        .where(EnrollmentIntentModel.intent_id == code.reserved_by_intent_id)
                        .with_for_update()
                    )
                    if intent is not None and intent.status in _OPEN_INTENT_STATUSES:
                        intent.status = "cancelled"
                        authority = await _authority_for_policy(
                            session, intent.policy_id, lock=True
                        )
                        if authority is not None and authority.reserved_tenant_count > 0:
                            authority.reserved_tenant_count -= 1
                        await session.execute(
                            update(EmailVerificationTokenModel)
                            .where(EmailVerificationTokenModel.intent_id == intent.intent_id)
                            .values(revoked_at=revoked_at)
                        )
                        await session.execute(
                            update(EnrollmentEmailDeliveryModel)
                            .where(EnrollmentEmailDeliveryModel.intent_id == intent.intent_id)
                            .values(status="cancelled")
                        )
                    code.reserved_by_intent_id = None
                    code.reserved_until = None
                session.add(
                    _platform_audit(
                        event_type="enrollment_code.revoked",
                        actor_subject_id=actor_subject_id,
                        correlation_id=correlation_id,
                        resource_type="enrollment_code",
                        resource_id=code_id,
                        details={},
                        created_at=revoked_at,
                    )
                )
            return _to_code(code)

    async def start_enrollment(
        self,
        *,
        code_digest: str,
        intent: EnrollmentIntent,
        verification: EmailVerificationToken,
        delivery: EnrollmentEmailDelivery,
        correlation_id: str,
        occurred_at: datetime,
    ) -> EnrollmentIntent:
        try:
            return await self._start_enrollment_inner(
                code_digest=code_digest,
                intent=intent,
                verification=verification,
                delivery=delivery,
                correlation_id=correlation_id,
                occurred_at=occurred_at,
            )
        except IntegrityError as exc:
            raise LookupError("enrollment is unavailable") from exc

    async def _start_enrollment_inner(
        self,
        *,
        code_digest: str,
        intent: EnrollmentIntent,
        verification: EmailVerificationToken,
        delivery: EnrollmentEmailDelivery,
        correlation_id: str,
        occurred_at: datetime,
    ) -> EnrollmentIntent:
        async with self._session_factory.begin() as session:
            existing_idempotency = await session.scalar(
                select(EnrollmentIntentModel)
                .where(EnrollmentIntentModel.idempotency_key_hash == intent.idempotency_key_hash)
                .with_for_update()
            )
            if existing_idempotency is not None:
                if existing_idempotency.request_hash != intent.request_hash:
                    raise ValueError("idempotency key was already used for another request")
                return _to_intent(existing_idempotency)

            policy = await session.scalar(
                select(TenantEnrollmentPolicyModel)
                .where(TenantEnrollmentPolicyModel.policy_id == intent.policy_id)
                .with_for_update()
            )
            if policy is None or not policy.enabled:
                raise LookupError("enrollment policy is not available")
            authority = await _authority_for_policy(session, intent.policy_id, lock=True)
            if authority is None or authority.status != "active":
                raise LookupError("enrollment authority is not available")
            code = await session.scalar(
                select(TenantEnrollmentCodeModel)
                .where(TenantEnrollmentCodeModel.code_digest == code_digest)
                .with_for_update()
            )
            if code is None or code.policy_id != intent.policy_id:
                raise LookupError("enrollment code is invalid")
            if code.expires_at <= occurred_at:
                code.status = "expired"
                raise LookupError("enrollment code is expired")
            if code.status in {"revoked", "consumed", "expired"}:
                raise LookupError("enrollment code is unavailable")
            if code.target_email != intent.normalized_email:
                raise LookupError("enrollment code is not valid for this email")

            if (
                code.status == "reserved"
                and code.reserved_until
                and code.reserved_until > occurred_at
            ):
                raise LookupError("enrollment code is already reserved")
            if code.status == "reserved" and code.reserved_by_intent_id:
                previous = await session.scalar(
                    select(EnrollmentIntentModel)
                    .where(EnrollmentIntentModel.intent_id == code.reserved_by_intent_id)
                    .with_for_update()
                )
                if previous is not None and previous.status in _OPEN_INTENT_STATUSES:
                    previous.status = "expired"
                    await session.execute(
                        update(EmailVerificationTokenModel)
                        .where(EmailVerificationTokenModel.intent_id == previous.intent_id)
                        .values(revoked_at=occurred_at)
                    )
                    if authority.reserved_tenant_count > 0:
                        authority.reserved_tenant_count -= 1

            open_email = await session.scalar(
                select(EnrollmentIntentModel)
                .where(
                    EnrollmentIntentModel.normalized_email == intent.normalized_email,
                    EnrollmentIntentModel.status.in_(_OPEN_INTENT_STATUSES),
                )
                .with_for_update()
            )
            if open_email is not None:
                if open_email.expires_at > occurred_at:
                    raise LookupError("an enrollment is already pending for this email")
                open_email.status = "expired"
                stale_code = await session.scalar(
                    select(TenantEnrollmentCodeModel)
                    .where(TenantEnrollmentCodeModel.code_id == open_email.code_id)
                    .with_for_update()
                )
                if (
                    stale_code is not None
                    and stale_code.reserved_by_intent_id == open_email.intent_id
                ):
                    stale_code.status = (
                        "expired" if stale_code.expires_at <= occurred_at else "issued"
                    )
                    stale_code.reserved_by_intent_id = None
                    stale_code.reserved_until = None
                stale_authority = authority
                if open_email.policy_id != intent.policy_id:
                    stale_authority = await _authority_for_policy(
                        session, open_email.policy_id, lock=True
                    )
                if stale_authority is not None and stale_authority.reserved_tenant_count > 0:
                    stale_authority.reserved_tenant_count -= 1
                await session.execute(
                    update(EmailVerificationTokenModel)
                    .where(EmailVerificationTokenModel.intent_id == open_email.intent_id)
                    .values(revoked_at=occurred_at)
                )
                await session.execute(
                    update(EnrollmentEmailDeliveryModel)
                    .where(EnrollmentEmailDeliveryModel.intent_id == open_email.intent_id)
                    .values(status="cancelled")
                )
            if (
                authority.active_tenant_count + authority.reserved_tenant_count
                >= authority.max_active_tenants
            ):
                raise LookupError("enrollment tenant quota is exhausted")
            if (
                authority.total_tenant_count + authority.reserved_tenant_count
                >= authority.max_total_tenants
            ):
                raise LookupError("enrollment tenant quota is exhausted")
            if intent.existing_user_id is not None:
                provisioning = await session.scalar(
                    select(TenantProvisioningModel)
                    .where(
                        TenantProvisioningModel.user_id == intent.existing_user_id,
                        TenantProvisioningModel.source == "self_service",
                    )
                    .with_for_update()
                )
                if provisioning is not None:
                    raise LookupError("this account already owns a self-service workspace")
            else:
                email_identity = await session.scalar(
                    select(EmailIdentityModel)
                    .where(EmailIdentityModel.normalized_email == intent.normalized_email)
                    .with_for_update()
                )
                if email_identity is not None:
                    raise LookupError("email identity already exists")

            authority.reserved_tenant_count += 1
            code.status = "reserved"
            code.reserved_by_intent_id = intent.intent_id
            code.reserved_until = occurred_at + timedelta(minutes=30)
            model = EnrollmentIntentModel(
                intent_id=intent.intent_id,
                policy_id=intent.policy_id,
                code_id=intent.code_id,
                normalized_email=intent.normalized_email,
                display_email=intent.display_email,
                display_name=intent.display_name,
                workspace_name=intent.workspace_name,
                password_hash=intent.password_hash,
                existing_user_id=intent.existing_user_id,
                proposed_user_id=intent.proposed_user_id,
                proposed_tenant_id=intent.proposed_tenant_id,
                request_hash=intent.request_hash,
                idempotency_key_hash=intent.idempotency_key_hash,
                status_token_hash=intent.status_token_hash,
                status="verification_sent",
                expires_at=intent.expires_at,
                created_at=intent.created_at,
            )
            session.add(model)
            await session.flush()
            session.add(
                EmailVerificationTokenModel(
                    token_id=verification.token_id,
                    intent_id=verification.intent_id,
                    token_hash=verification.token_hash,
                    expires_at=verification.expires_at,
                    created_at=verification.created_at,
                )
            )
            session.add(
                EnrollmentEmailDeliveryModel(
                    delivery_id=delivery.delivery_id,
                    intent_id=delivery.intent_id,
                    token_id=delivery.token_id,
                    recipient=delivery.recipient,
                    display_name=delivery.display_name,
                    workspace_name=delivery.workspace_name,
                    token_expires_at=delivery.token_expires_at,
                    status="pending",
                    attempts=0,
                    available_at=delivery.available_at,
                    created_at=delivery.created_at,
                )
            )
            session.add(
                _platform_audit(
                    event_type="enrollment.started",
                    actor_subject_id=None,
                    correlation_id=correlation_id,
                    resource_type="enrollment_intent",
                    resource_id=intent.intent_id,
                    details={
                        "policy_id": intent.policy_id,
                        "email_hash": _audit_hash(intent.normalized_email),
                        "existing_user": intent.existing_user_id is not None,
                    },
                    created_at=occurred_at,
                )
            )
            return _to_intent(model)

    async def resend_verification(
        self,
        *,
        status_token_hash: str,
        verification: EmailVerificationToken,
        delivery: EnrollmentEmailDelivery,
        correlation_id: str,
        occurred_at: datetime,
    ) -> EnrollmentIntent | None:
        async with self._session_factory.begin() as session:
            intent = await session.scalar(
                select(EnrollmentIntentModel)
                .where(EnrollmentIntentModel.status_token_hash == status_token_hash)
                .with_for_update()
            )
            if intent is None or intent.status not in _OPEN_INTENT_STATUSES:
                return None
            if intent.expires_at <= occurred_at:
                intent.status = "expired"
                return None
            await session.execute(
                update(EmailVerificationTokenModel)
                .where(
                    EmailVerificationTokenModel.intent_id == intent.intent_id,
                    EmailVerificationTokenModel.consumed_at.is_(None),
                    EmailVerificationTokenModel.revoked_at.is_(None),
                )
                .values(revoked_at=occurred_at)
            )
            await session.execute(
                update(EnrollmentEmailDeliveryModel)
                .where(
                    EnrollmentEmailDeliveryModel.intent_id == intent.intent_id,
                    EnrollmentEmailDeliveryModel.status.in_(("pending", "processing", "failed")),
                )
                .values(status="cancelled")
            )
            intent.status = "verification_sent"
            session.add(
                EmailVerificationTokenModel(
                    token_id=verification.token_id,
                    intent_id=verification.intent_id,
                    token_hash=verification.token_hash,
                    expires_at=verification.expires_at,
                    created_at=verification.created_at,
                )
            )
            session.add(
                EnrollmentEmailDeliveryModel(
                    delivery_id=delivery.delivery_id,
                    intent_id=delivery.intent_id,
                    token_id=delivery.token_id,
                    recipient=delivery.recipient,
                    display_name=delivery.display_name,
                    workspace_name=delivery.workspace_name,
                    token_expires_at=delivery.token_expires_at,
                    status="pending",
                    attempts=0,
                    available_at=delivery.available_at,
                    created_at=delivery.created_at,
                )
            )
            session.add(
                _platform_audit(
                    event_type="enrollment.verification_resent",
                    actor_subject_id=None,
                    correlation_id=correlation_id,
                    resource_type="enrollment_intent",
                    resource_id=intent.intent_id,
                    details={},
                    created_at=occurred_at,
                )
            )
            return _to_intent(intent)

    async def get_enrollment_status(
        self,
        *,
        status_token_hash: str,
        checked_at: datetime,
    ) -> EnrollmentIntent | None:
        async with self._session_factory() as session:
            model = await session.scalar(
                select(EnrollmentIntentModel).where(
                    EnrollmentIntentModel.status_token_hash == status_token_hash
                )
            )
        if model is None:
            return None
        return _to_intent(model, expire_at_check=checked_at)

    async def complete_enrollment(
        self,
        *,
        verification_token_hash: str,
        correlation_id: str,
        completed_at: datetime,
    ) -> EnrollmentCompletion | None:
        async with self._session_factory.begin() as session:
            row = (
                await session.execute(
                    select(
                        EnrollmentIntentModel,
                        EmailVerificationTokenModel,
                        TenantEnrollmentCodeModel,
                        TenantEnrollmentPolicyModel,
                        EnrollmentAuthorityModel,
                    )
                    .join(
                        EmailVerificationTokenModel,
                        EmailVerificationTokenModel.intent_id == EnrollmentIntentModel.intent_id,
                    )
                    .join(
                        TenantEnrollmentCodeModel,
                        TenantEnrollmentCodeModel.code_id == EnrollmentIntentModel.code_id,
                    )
                    .join(
                        TenantEnrollmentPolicyModel,
                        TenantEnrollmentPolicyModel.policy_id == EnrollmentIntentModel.policy_id,
                    )
                    .join(
                        EnrollmentAuthorityModel,
                        EnrollmentAuthorityModel.authority_id
                        == TenantEnrollmentPolicyModel.authority_id,
                    )
                    .where(EmailVerificationTokenModel.token_hash == verification_token_hash)
                    .with_for_update()
                )
            ).first()
            if row is None:
                return None
            intent, token, code, policy, authority = row
            if token.consumed_at is not None or token.revoked_at is not None:
                return None
            if token.expires_at <= completed_at or intent.expires_at <= completed_at:
                intent.status = "expired"
                return None
            if intent.status not in _OPEN_INTENT_STATUSES:
                return None
            if (
                not policy.enabled
                or authority.status != "active"
                or code.status != "reserved"
                or code.reserved_by_intent_id != intent.intent_id
                or authority.reserved_tenant_count <= 0
            ):
                return None
            if intent.existing_user_id is not None:
                user_model = await session.scalar(
                    select(IdentityUserModel)
                    .where(IdentityUserModel.user_id == intent.existing_user_id)
                    .with_for_update()
                )
                email_model = await session.scalar(
                    select(EmailIdentityModel)
                    .where(
                        EmailIdentityModel.user_id == intent.existing_user_id,
                        EmailIdentityModel.normalized_email == intent.normalized_email,
                    )
                    .with_for_update()
                )
                if user_model is None or email_model is None or email_model.status != "active":
                    return None
                if user_model.status != "active":
                    return None
            else:
                conflicting_email = await session.scalar(
                    select(EmailIdentityModel)
                    .where(EmailIdentityModel.normalized_email == intent.normalized_email)
                    .with_for_update()
                )
                if conflicting_email is not None:
                    raise LookupError("email identity already exists")
                user_model = IdentityUserModel(
                    user_id=intent.proposed_user_id,
                    display_name=intent.display_name,
                    status="active",
                    created_at=completed_at,
                    updated_at=completed_at,
                )
                email_model = EmailIdentityModel(
                    identity_id=str(uuid4()),
                    user_id=user_model.user_id,
                    normalized_email=intent.normalized_email,
                    display_email=intent.display_email,
                    status="active",
                    verified_at=completed_at,
                    created_at=completed_at,
                    updated_at=completed_at,
                )
                session.add(user_model)
                await session.flush()
                session.add(email_model)
                session.add(
                    PasswordCredentialModel(
                        user_id=user_model.user_id,
                        password_hash=intent.password_hash or "",
                        password_version=1,
                        changed_at=completed_at,
                        created_at=completed_at,
                    )
                )
                await session.flush()

            tenant = TenantModel(
                tenant_id=intent.proposed_tenant_id,
                name=intent.workspace_name,
                status="active",
                created_at=completed_at,
                updated_at=completed_at,
            )
            session.add(tenant)
            await session.flush()
            await _set_transaction_tenant(session, tenant.tenant_id)
            session.add(
                TenantSettingsModel(
                    tenant_id=tenant.tenant_id,
                    primary_language="zh-CN",
                    timezone="Asia/Shanghai",
                    conversation_retention_days=180,
                    notification_settings={},
                    created_at=completed_at,
                    updated_at=completed_at,
                )
            )
            subscription = TenantSubscriptionModel(
                tenant_id=tenant.tenant_id,
                plan_id=policy.default_plan_id,
                status="trial",
                created_at=completed_at,
                updated_at=completed_at,
            )
            quota = TenantQuotaModel(
                tenant_id=tenant.tenant_id,
                site_limit=policy.site_limit,
                created_at=completed_at,
                updated_at=completed_at,
            )
            session.add_all((subscription, quota))
            for queue_id, name, description, is_default in (
                ("general", "通用客服", "默认客服队列", True),
                ("orders", "订单人工客服", "订单、物流、退款、取消、支付和地址问题", False),
            ):
                session.add(
                    SupportQueueModel(
                        tenant_id=tenant.tenant_id,
                        queue_id=queue_id,
                        name=name,
                        description=description,
                        is_default=is_default,
                        status="active",
                        created_at=completed_at,
                        updated_at=completed_at,
                    )
                )
            membership_id = str(
                uuid5(NAMESPACE_URL, f"membership:{tenant.tenant_id}:{user_model.user_id}")
            )
            roles = frozenset({"tenant_owner"})
            session.add(
                TenantMembershipModel(
                    membership_id=membership_id,
                    tenant_id=tenant.tenant_id,
                    user_id=user_model.user_id,
                    roles=sorted(roles),
                    scopes=sorted(scopes_for_roles(roles)),
                    status="active",
                    source="self_service",
                    approval_status="approved",
                    activated_at=completed_at,
                    deactivated_at=None,
                    created_by=user_model.user_id,
                    created_at=completed_at,
                    updated_at=completed_at,
                )
            )
            session.add(
                TenantProvisioningModel(
                    provisioning_id=intent.intent_id,
                    user_id=user_model.user_id,
                    tenant_id=tenant.tenant_id,
                    source="self_service",
                    status="completed",
                    created_at=completed_at,
                    completed_at=completed_at,
                )
            )
            code.status = "consumed"
            code.consumed_by_user_id = user_model.user_id
            code.consumed_tenant_id = tenant.tenant_id
            code.consumed_at = completed_at
            code.reserved_by_intent_id = None
            code.reserved_until = None
            authority.reserved_tenant_count -= 1
            authority.active_tenant_count += 1
            authority.total_tenant_count += 1
            intent.status = "completed"
            intent.completed_at = completed_at
            token.consumed_at = completed_at
            await session.execute(
                update(EnrollmentEmailDeliveryModel)
                .where(
                    EnrollmentEmailDeliveryModel.intent_id == intent.intent_id,
                    EnrollmentEmailDeliveryModel.status.in_(("pending", "processing", "failed")),
                )
                .values(status="cancelled")
            )
            session.add(
                PlatformAuditEventModel(
                    event_id=str(uuid5(NAMESPACE_URL, f"enrollment.completed:{intent.intent_id}")),
                    event_type="enrollment.completed",
                    actor_subject_id=user_model.user_id,
                    correlation_id=correlation_id,
                    resource_type="tenant_provisioning",
                    resource_id=intent.intent_id,
                    details={"tenant_id": tenant.tenant_id, "user_id": user_model.user_id},
                    created_at=completed_at,
                )
            )
            session.add(
                AuditEventModel(
                    tenant_id=tenant.tenant_id,
                    event_id=str(uuid5(NAMESPACE_URL, f"tenant-created:{intent.intent_id}")),
                    event_type="tenant.created_by_self_service",
                    actor_subject_id=user_model.user_id,
                    correlation_id=correlation_id,
                    resource_type="tenant",
                    resource_id=tenant.tenant_id,
                    details={"workspace_name": tenant.name, "source": "self_service"},
                    created_at=completed_at,
                )
            )
            return EnrollmentCompletion(
                intent_id=intent.intent_id,
                user_id=user_model.user_id,
                tenant_id=tenant.tenant_id,
                workspace_name=tenant.name,
                completed_at=completed_at,
            )

    async def claim_email_deliveries(
        self,
        *,
        claimed_at: datetime,
        lease_seconds: int,
        limit: int,
    ) -> list[EnrollmentEmailDelivery]:
        async with self._session_factory.begin() as session:
            rows = list(
                (
                    await session.execute(
                        select(EnrollmentEmailDeliveryModel)
                        .join(
                            EnrollmentIntentModel,
                            EnrollmentIntentModel.intent_id
                            == EnrollmentEmailDeliveryModel.intent_id,
                        )
                        .join(
                            EmailVerificationTokenModel,
                            EmailVerificationTokenModel.token_id
                            == EnrollmentEmailDeliveryModel.token_id,
                        )
                        .where(
                            EnrollmentIntentModel.status.in_(_OPEN_INTENT_STATUSES),
                            EmailVerificationTokenModel.consumed_at.is_(None),
                            EmailVerificationTokenModel.revoked_at.is_(None),
                            EnrollmentEmailDeliveryModel.available_at <= claimed_at,
                            (
                                EnrollmentEmailDeliveryModel.status.in_(("pending", "failed"))
                                | (EnrollmentEmailDeliveryModel.status == "processing")
                                & (EnrollmentEmailDeliveryModel.lease_until < claimed_at)
                            ),
                        )
                        .order_by(EnrollmentEmailDeliveryModel.id)
                        .with_for_update(skip_locked=True)
                        .limit(limit)
                    )
                ).scalars()
            )
            lease_until = claimed_at + timedelta(seconds=lease_seconds)
            for model in rows:
                model.status = "processing"
                model.attempts += 1
                model.lease_until = lease_until
            return [_to_delivery(model) for model in rows]

    async def mark_email_delivered(self, *, delivery_id: str, delivered_at: datetime) -> None:
        async with self._session_factory.begin() as session:
            await session.execute(
                update(EnrollmentEmailDeliveryModel)
                .where(EnrollmentEmailDeliveryModel.delivery_id == delivery_id)
                .values(status="sent", sent_at=delivered_at, lease_until=None, last_error=None)
            )

    async def mark_email_failed(
        self,
        *,
        delivery_id: str,
        error_code: str,
        retry_at: datetime,
    ) -> None:
        async with self._session_factory.begin() as session:
            await session.execute(
                update(EnrollmentEmailDeliveryModel)
                .where(EnrollmentEmailDeliveryModel.delivery_id == delivery_id)
                .values(
                    status="failed",
                    available_at=retry_at,
                    lease_until=None,
                    last_error=error_code[:500],
                )
            )


async def _authority_for_policy(
    session: AsyncSession, policy_id: str, *, lock: bool
) -> EnrollmentAuthorityModel | None:
    query = (
        select(EnrollmentAuthorityModel)
        .join(
            TenantEnrollmentPolicyModel,
            TenantEnrollmentPolicyModel.authority_id == EnrollmentAuthorityModel.authority_id,
        )
        .where(TenantEnrollmentPolicyModel.policy_id == policy_id)
    )
    if lock:
        query = query.with_for_update()
    return await session.scalar(query)


async def _set_transaction_tenant(session: AsyncSession, tenant_id: str) -> None:
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"), {"tenant_id": tenant_id}
    )


def _platform_audit(
    *,
    event_type: str,
    actor_subject_id: str | None,
    correlation_id: str | None,
    resource_type: str,
    resource_id: str,
    details: dict,
    created_at: datetime,
) -> PlatformAuditEventModel:
    return PlatformAuditEventModel(
        event_id=str(uuid4()),
        event_type=event_type,
        actor_subject_id=actor_subject_id,
        correlation_id=correlation_id,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details,
        created_at=created_at,
    )


def _audit_hash(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _to_authority(model: EnrollmentAuthorityModel) -> EnrollmentAuthority:
    return EnrollmentAuthority(
        authority_id=model.authority_id,
        name=model.name,
        status=model.status,
        max_active_tenants=model.max_active_tenants,
        max_total_tenants=model.max_total_tenants,
        active_tenant_count=model.active_tenant_count,
        reserved_tenant_count=model.reserved_tenant_count,
        total_tenant_count=model.total_tenant_count,
        created_by=model.created_by,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _to_policy(model: TenantEnrollmentPolicyModel) -> TenantEnrollmentPolicy:
    return TenantEnrollmentPolicy(
        policy_id=model.policy_id,
        authority_id=model.authority_id,
        name=model.name,
        enabled=model.enabled,
        allowed_email_domains=tuple(str(item).casefold() for item in model.allowed_email_domains),
        require_exact_email_binding=model.require_exact_email_binding,
        default_plan_id=model.default_plan_id,
        require_email_verification=model.require_email_verification,
        require_mfa=model.require_mfa,
        site_limit=model.site_limit,
        created_by=model.created_by,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _to_code(model: TenantEnrollmentCodeModel) -> TenantEnrollmentCode:
    return TenantEnrollmentCode(
        code_id=model.code_id,
        policy_id=model.policy_id,
        code_digest=model.code_digest,
        code_key_version=model.code_key_version,
        target_email=model.target_email,
        code_prefix=model.code_prefix,
        status=model.status,
        expires_at=model.expires_at,
        created_by=model.created_by,
        created_at=model.created_at,
        reserved_by_intent_id=model.reserved_by_intent_id,
        reserved_until=model.reserved_until,
        consumed_by_user_id=model.consumed_by_user_id,
        consumed_tenant_id=model.consumed_tenant_id,
        consumed_at=model.consumed_at,
        revoked_by=model.revoked_by,
        revoked_at=model.revoked_at,
    )


def _to_intent(
    model: EnrollmentIntentModel, *, expire_at_check: datetime | None = None
) -> EnrollmentIntent:
    status = model.status
    if (
        expire_at_check is not None
        and model.expires_at <= expire_at_check
        and status in _OPEN_INTENT_STATUSES
    ):
        status = "expired"
    return EnrollmentIntent(
        intent_id=model.intent_id,
        policy_id=model.policy_id,
        code_id=model.code_id,
        normalized_email=model.normalized_email,
        display_email=model.display_email,
        display_name=model.display_name,
        workspace_name=model.workspace_name,
        password_hash=model.password_hash,
        existing_user_id=model.existing_user_id,
        proposed_user_id=model.proposed_user_id,
        proposed_tenant_id=model.proposed_tenant_id,
        request_hash=model.request_hash,
        idempotency_key_hash=model.idempotency_key_hash,
        status_token_hash=model.status_token_hash,
        status=status,
        expires_at=model.expires_at,
        created_at=model.created_at,
        completed_at=model.completed_at,
    )


def _to_delivery(model: EnrollmentEmailDeliveryModel) -> EnrollmentEmailDelivery:
    return EnrollmentEmailDelivery(
        delivery_id=model.delivery_id,
        intent_id=model.intent_id,
        token_id=model.token_id,
        recipient=model.recipient,
        display_name=model.display_name,
        workspace_name=model.workspace_name,
        token_expires_at=model.token_expires_at,
        status=model.status,
        attempts=model.attempts,
        available_at=model.available_at,
        created_at=model.created_at,
        lease_until=model.lease_until,
        sent_at=model.sent_at,
        last_error=model.last_error,
    )
