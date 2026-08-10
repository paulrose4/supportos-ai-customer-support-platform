import asyncio
import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Final
from urllib.parse import quote
from uuid import uuid4

from app.application.dto.onboarding import (
    CreateEnrollmentAuthorityCommand,
    CreateEnrollmentPolicyCommand,
    GetSelfServiceSignupStatusQuery,
    IssueEnrollmentCodeCommand,
    IssueEnrollmentCodeResult,
    IssueWorkspaceOnboardingCodeCommand,
    IssueWorkspaceOnboardingCodeResult,
    ListEnrollmentCodesQuery,
    ListEnrollmentCodesResult,
    ResendSelfServiceVerificationCommand,
    RevokeEnrollmentCodeCommand,
    SelfServiceSignupStatusResult,
    StartSelfServiceSignupCommand,
    StartSelfServiceSignupResult,
    VerifySelfServiceEmailCommand,
    VerifySelfServiceEmailResult,
)
from app.application.services.email_identity import (
    _validate_password,
    normalize_email,
)
from app.domain.models.onboarding import (
    EmailVerificationToken,
    EnrollmentAuthority,
    EnrollmentEmailDelivery,
    EnrollmentIntent,
    TenantEnrollmentCode,
    TenantEnrollmentPolicy,
)
from app.domain.ports import (
    EmailIdentityStorePort,
    EnrollmentRateLimitPort,
    PasswordHasherPort,
    SelfServiceOnboardingStorePort,
    TransactionalEmailPort,
)

_CODE_ALPHABET: Final = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
_PLATFORM_ROLES: Final = frozenset({"platform_owner", "platform_operator", "platform_auditor"})


class EnrollmentAccessError(PermissionError):
    """A deliberately generic public error for enrollment eligibility failures."""


class EnrollmentConflictError(ValueError):
    """A retry or request conflict that should not create side effects."""


class EnrollmentRateLimitedError(PermissionError):
    def __init__(self, retry_after_seconds: int = 60) -> None:
        super().__init__("too many enrollment attempts")
        self.retry_after_seconds = max(1, retry_after_seconds)


class SelfServiceTenantProvisioningService:
    def __init__(
        self,
        *,
        store: SelfServiceOnboardingStorePort,
        identity_store: EmailIdentityStorePort,
        password_hasher: PasswordHasherPort,
        rate_limits: EnrollmentRateLimitPort,
        token_secret: str,
        public_base_url: str,
        intent_ttl_seconds: int = 1800,
        verification_ttl_seconds: int = 1800,
        resend_limit_per_day: int = 5,
    ) -> None:
        if len(token_secret) < 32:
            raise ValueError("enrollment token secret must contain at least 32 characters")
        if intent_ttl_seconds < 300 or verification_ttl_seconds < 300:
            raise ValueError("enrollment token TTLs must be at least 300 seconds")
        self._store = store
        self._identity_store = identity_store
        self._password_hasher = password_hasher
        self._rate_limits = rate_limits
        self._secret = token_secret.encode("utf-8")
        self._public_base_url = public_base_url.rstrip("/")
        self._intent_ttl = timedelta(seconds=intent_ttl_seconds)
        self._verification_ttl = timedelta(seconds=verification_ttl_seconds)
        self._resend_limit_per_day = resend_limit_per_day

    async def start_signup(
        self, command: StartSelfServiceSignupCommand
    ) -> StartSelfServiceSignupResult:
        now = datetime.now(UTC)
        cleanup = getattr(self._store, "expire_enrollment_intents", None)
        if cleanup is not None:
            await cleanup(expired_at=now)
        normalized_email = normalize_email(command.email)
        _validate_password(command.password)
        display_name = _clean_label(command.display_name, "display name")
        workspace_name = _clean_label(command.workspace_name, "workspace name")
        idempotency_key = command.idempotency_key.strip()
        if len(idempotency_key) < 16 or len(idempotency_key) > 200:
            raise ValueError("Idempotency-Key must contain between 16 and 200 characters")
        code_value = command.enrollment_code.strip().replace("-", "").replace(" ", "")
        if len(code_value) < 20:
            raise EnrollmentAccessError("enrollment is unavailable")
        code_digest = self._digest("code", code_value)
        await self._require_limit(
            bucket="enrollment.signup.ip",
            source_key=command.source_fingerprint,
            limit=10,
            window_seconds=900,
            now=now,
        )
        await self._require_limit(
            bucket="enrollment.signup.email",
            source_key=self._digest("email", normalized_email),
            limit=5,
            window_seconds=3600,
            now=now,
        )
        await self._require_limit(
            bucket="enrollment.signup.code",
            source_key=code_digest,
            limit=20,
            window_seconds=900,
            now=now,
        )
        code = await self._store.get_code_by_digest(code_digest=code_digest, checked_at=now)
        if code is None:
            raise EnrollmentAccessError("enrollment is unavailable")
        policy = await self._store.get_policy(policy_id=code.policy_id)
        if policy is None or not policy.enabled or not _email_allowed(normalized_email, policy):
            raise EnrollmentAccessError("enrollment is unavailable")
        if code.target_email and code.target_email != normalized_email:
            raise EnrollmentAccessError("enrollment is unavailable")

        account = await self._identity_store.get_login_account(normalized_email=normalized_email)
        existing_user_id: str | None = None
        password_hash: str | None = None
        if account is not None:
            if (
                account.user.status != "active"
                or account.identity.status != "active"
                or account.identity.verified_at is None
                or not self._password_hasher.verify_password(
                    command.password, account.password_hash
                )
            ):
                raise EnrollmentAccessError("enrollment is unavailable")
            existing_user_id = account.user.user_id
        else:
            password_hash = self._password_hasher.hash_password(command.password)

        request_hash = self._digest(
            "request",
            "|".join(
                (
                    normalized_email,
                    display_name,
                    workspace_name,
                    code_digest,
                    self._digest("password", command.password),
                )
            ),
        )
        idempotency_hash = self._digest("idempotency", idempotency_key)
        intent_id = str(uuid4())
        proposed_user_id = existing_user_id or str(uuid4())
        proposed_tenant_id = f"tenant-{secrets.token_hex(16)}"
        status_token = self._status_token(idempotency_key)
        status_token_hash = self._digest("status", status_token)
        expires_at = now + self._intent_ttl
        verification = _new_verification_token(
            intent_id=intent_id,
            expires_at=min(expires_at, now + self._verification_ttl),
            secret=self._secret,
        )
        intent = EnrollmentIntent(
            intent_id=intent_id,
            policy_id=policy.policy_id,
            code_id=code.code_id,
            normalized_email=normalized_email,
            display_email=command.email.strip(),
            display_name=display_name,
            workspace_name=workspace_name,
            password_hash=password_hash,
            existing_user_id=existing_user_id,
            proposed_user_id=proposed_user_id,
            proposed_tenant_id=proposed_tenant_id,
            request_hash=request_hash,
            idempotency_key_hash=idempotency_hash,
            status_token_hash=status_token_hash,
            status="created",
            expires_at=expires_at,
            created_at=now,
        )
        delivery = EnrollmentEmailDelivery(
            delivery_id=str(uuid4()),
            intent_id=intent_id,
            token_id=verification.token_id,
            recipient=command.email.strip(),
            display_name=display_name,
            workspace_name=workspace_name,
            token_expires_at=verification.expires_at,
            status="pending",
            attempts=0,
            available_at=now,
            created_at=now,
        )
        try:
            persisted = await self._store.start_enrollment(
                code_digest=code_digest,
                intent=intent,
                verification=verification,
                delivery=delivery,
                correlation_id=command.correlation_id,
                occurred_at=now,
            )
        except (LookupError, ValueError) as exc:
            if "idempotency key" in str(exc):
                raise EnrollmentConflictError(str(exc)) from exc
            raise EnrollmentAccessError("enrollment is unavailable") from exc
        return StartSelfServiceSignupResult(
            status=persisted.status,
            status_token=status_token,
            expires_at=persisted.expires_at,
        )

    async def verify_email(
        self, command: VerifySelfServiceEmailCommand
    ) -> VerifySelfServiceEmailResult:
        now = datetime.now(UTC)
        token = command.verification_token.strip()
        if len(token) < 40 or len(token) > 300:
            raise EnrollmentAccessError("verification link is invalid or expired")
        token_hash = self._digest("verification", token)
        await self._require_limit(
            bucket="enrollment.verify.ip",
            source_key=command.source_fingerprint,
            limit=20,
            window_seconds=900,
            now=now,
        )
        await self._require_limit(
            bucket="enrollment.verify.token",
            source_key=token_hash,
            limit=10,
            window_seconds=900,
            now=now,
        )
        completion = await self._store.complete_enrollment(
            verification_token_hash=token_hash,
            correlation_id=command.correlation_id,
            completed_at=now,
        )
        if completion is None:
            raise EnrollmentAccessError("verification link is invalid or expired")
        return VerifySelfServiceEmailResult(
            status="completed",
            tenant_id=completion.tenant_id,
            workspace_name=completion.workspace_name,
        )

    async def resend_verification(
        self, command: ResendSelfServiceVerificationCommand
    ) -> StartSelfServiceSignupResult:
        now = datetime.now(UTC)
        status_token = command.status_token.strip()
        await self._require_limit(
            bucket="enrollment.resend.ip",
            source_key=command.source_fingerprint,
            limit=10,
            window_seconds=900,
            now=now,
        )
        intent = await self._store.get_enrollment_status(
            status_token_hash=self._digest("status", status_token), checked_at=now
        )
        if intent is None or intent.status not in {"created", "verification_sent"}:
            raise EnrollmentAccessError("enrollment is unavailable")
        await self._require_limit(
            bucket="enrollment.resend.email",
            source_key=self._digest("email", intent.normalized_email),
            limit=self._resend_limit_per_day,
            window_seconds=86400,
            now=now,
        )
        expires_at = min(intent.expires_at, now + self._verification_ttl)
        verification = _new_verification_token(
            intent_id=intent.intent_id,
            expires_at=expires_at,
            secret=self._secret,
        )
        delivery = EnrollmentEmailDelivery(
            delivery_id=str(uuid4()),
            intent_id=intent.intent_id,
            token_id=verification.token_id,
            recipient=intent.display_email,
            display_name=intent.display_name,
            workspace_name=intent.workspace_name,
            token_expires_at=verification.expires_at,
            status="pending",
            attempts=0,
            available_at=now,
            created_at=now,
        )
        updated = await self._store.resend_verification(
            status_token_hash=self._digest("status", status_token),
            verification=verification,
            delivery=delivery,
            correlation_id=command.correlation_id,
            occurred_at=now,
        )
        if updated is None:
            raise EnrollmentAccessError("enrollment is unavailable")
        return StartSelfServiceSignupResult(
            status=updated.status,
            status_token=status_token,
            expires_at=updated.expires_at,
        )

    async def get_status(
        self, query: GetSelfServiceSignupStatusQuery
    ) -> SelfServiceSignupStatusResult:
        now = datetime.now(UTC)
        intent = await self._store.get_enrollment_status(
            status_token_hash=self._digest("status", query.status_token.strip()),
            checked_at=now,
        )
        if intent is None:
            raise EnrollmentAccessError("enrollment is unavailable")
        return SelfServiceSignupStatusResult(
            status=intent.status,
            expires_at=intent.expires_at,
            tenant_id=intent.proposed_tenant_id if intent.status == "completed" else None,
            workspace_name=intent.workspace_name,
        )

    async def create_authority(
        self, command: CreateEnrollmentAuthorityCommand
    ) -> EnrollmentAuthority:
        _require_platform(command.principal.platform_roles)
        name = _clean_label(command.name, "authority name")
        if not 1 <= command.max_active_tenants <= command.max_total_tenants:
            raise ValueError("tenant limits are invalid")
        now = datetime.now(UTC)
        return await self._store.create_authority(
            EnrollmentAuthority(
                authority_id=_clean_id(command.authority_id, "authority_id"),
                name=name,
                status="active",
                max_active_tenants=command.max_active_tenants,
                max_total_tenants=command.max_total_tenants,
                active_tenant_count=0,
                reserved_tenant_count=0,
                total_tenant_count=0,
                created_by=command.principal.subject_id,
                created_at=now,
                updated_at=now,
            ),
            correlation_id=command.correlation_id,
        )

    async def create_policy(self, command: CreateEnrollmentPolicyCommand) -> TenantEnrollmentPolicy:
        _require_platform(command.principal.platform_roles)
        domains = tuple(_normalize_domain(item) for item in command.allowed_email_domains)
        if not domains:
            raise ValueError("at least one allowed email domain is required")
        if command.default_plan_id.strip() not in {"trial", "standard", "enterprise"}:
            raise ValueError("default plan is invalid")
        now = datetime.now(UTC)
        return await self._store.create_policy(
            TenantEnrollmentPolicy(
                policy_id=_clean_id(command.policy_id, "policy_id"),
                authority_id=_clean_id(command.authority_id, "authority_id"),
                name=_clean_label(command.name, "policy name"),
                enabled=True,
                allowed_email_domains=domains,
                require_exact_email_binding=command.require_exact_email_binding,
                default_plan_id=command.default_plan_id.strip(),
                require_email_verification=True,
                require_mfa=command.require_mfa,
                site_limit=max(1, min(command.site_limit, 100)),
                created_by=command.principal.subject_id,
                created_at=now,
                updated_at=now,
            ),
            correlation_id=command.correlation_id,
        )

    async def issue_code(self, command: IssueEnrollmentCodeCommand) -> IssueEnrollmentCodeResult:
        _require_platform(command.principal.platform_roles)
        normalized_email = normalize_email(command.target_email)
        if not 1 <= command.expires_in_hours <= 168:
            raise ValueError("code expiry must be between 1 and 168 hours")
        raw_code = _new_code()
        now = datetime.now(UTC)
        model = TenantEnrollmentCode(
            code_id=str(uuid4()),
            policy_id=_clean_id(command.policy_id, "policy_id"),
            code_digest=self._digest("code", raw_code),
            code_key_version="v1",
            target_email=normalized_email,
            code_prefix=raw_code[:8],
            status="issued",
            expires_at=now + timedelta(hours=command.expires_in_hours),
            created_by=command.principal.subject_id,
            created_at=now,
        )
        created = await self._store.create_code(model, correlation_id=command.correlation_id)
        return IssueEnrollmentCodeResult(code=created, enrollment_code=raw_code)

    async def issue_workspace_onboarding_code(
        self, command: IssueWorkspaceOnboardingCodeCommand
    ) -> IssueWorkspaceOnboardingCodeResult:
        _require_platform(command.principal.platform_roles)
        normalized_email = normalize_email(command.target_email)
        if not 1 <= command.expires_in_hours <= 168:
            raise ValueError("code expiry must be between 1 and 168 hours")
        if not 1 <= command.site_limit <= 100:
            raise ValueError("site limit must be between 1 and 100")

        domain = normalized_email.rsplit("@", 1)[1]
        authority_id = "self-service-default"
        domain_key = hashlib.sha256(domain.encode("utf-8")).hexdigest()[:24]
        policy_id = f"self-service-{domain_key}-{command.site_limit}"
        now = datetime.now(UTC)
        authority = EnrollmentAuthority(
            authority_id=authority_id,
            name="Self-service workspace onboarding",
            status="active",
            max_active_tenants=100_000,
            max_total_tenants=100_000,
            active_tenant_count=0,
            reserved_tenant_count=0,
            total_tenant_count=0,
            created_by=command.principal.subject_id,
            created_at=now,
            updated_at=now,
        )
        policy = TenantEnrollmentPolicy(
            policy_id=policy_id,
            authority_id=authority_id,
            name=f"Self-service {domain} workspace onboarding",
            enabled=True,
            allowed_email_domains=(domain,),
            require_exact_email_binding=True,
            default_plan_id="trial",
            require_email_verification=True,
            require_mfa=False,
            site_limit=command.site_limit,
            created_by=command.principal.subject_id,
            created_at=now,
            updated_at=now,
        )
        ensure_setup = getattr(self._store, "ensure_authority_and_policy", None)
        if ensure_setup is not None:
            await ensure_setup(authority, policy, correlation_id=command.correlation_id)
        else:
            await self._store.create_authority(authority, correlation_id=command.correlation_id)
            await self._store.create_policy(policy, correlation_id=command.correlation_id)
        issued = await self.issue_code(
            IssueEnrollmentCodeCommand(
                principal=command.principal,
                policy_id=policy_id,
                target_email=normalized_email,
                expires_in_hours=command.expires_in_hours,
                correlation_id=command.correlation_id,
            )
        )
        signup_url = (
            f"{self._public_base_url}/#signup_code={issued.enrollment_code}"
            f"&email={quote(normalized_email, safe='')}"
        )
        return IssueWorkspaceOnboardingCodeResult(
            code=issued.code,
            enrollment_code=issued.enrollment_code,
            signup_url=signup_url,
        )

    async def list_codes(self, query: ListEnrollmentCodesQuery) -> ListEnrollmentCodesResult:
        _require_platform(query.principal.platform_roles)
        return ListEnrollmentCodesResult(
            tuple(await self._store.list_codes(policy_id=query.policy_id))
        )

    async def revoke_code(self, command: RevokeEnrollmentCodeCommand) -> TenantEnrollmentCode:
        _require_platform(command.principal.platform_roles)
        code = await self._store.revoke_code(
            code_id=command.code_id,
            actor_subject_id=command.principal.subject_id,
            correlation_id=command.correlation_id,
            revoked_at=datetime.now(UTC),
        )
        if code is None:
            raise LookupError("enrollment code was not found")
        return code

    async def _require_limit(
        self,
        *,
        bucket: str,
        source_key: str,
        limit: int,
        window_seconds: int,
        now: datetime,
    ) -> None:
        if not await self._rate_limits.consume(
            bucket=bucket,
            source_key=source_key,
            limit=limit,
            window_seconds=window_seconds,
            occurred_at=now,
        ):
            raise EnrollmentRateLimitedError()

    def _digest(self, purpose: str, value: str) -> str:
        return hmac.new(self._secret, f"{purpose}:{value}".encode(), hashlib.sha256).hexdigest()

    def _status_token(self, idempotency_key: str) -> str:
        digest = hmac.new(
            self._secret, f"status-token:{idempotency_key}".encode(), hashlib.sha256
        ).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


class SelfServiceEnrollmentEmailWorker:
    def __init__(
        self,
        *,
        store: SelfServiceOnboardingStorePort,
        transactional_email: TransactionalEmailPort,
        token_secret: str,
        public_base_url: str,
        poll_seconds: float = 5.0,
        lease_seconds: int = 120,
        max_attempts: int = 5,
    ) -> None:
        self._store = store
        self._email = transactional_email
        self._secret = token_secret.encode("utf-8")
        self._public_base_url = public_base_url.rstrip("/")
        self._poll_seconds = poll_seconds
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts

    async def run_once(self) -> int:
        deliveries = await self._store.claim_email_deliveries(
            claimed_at=datetime.now(UTC),
            lease_seconds=self._lease_seconds,
            limit=20,
        )
        for delivery in deliveries:
            try:
                token = _verification_token(
                    delivery.token_id,
                    delivery.intent_id,
                    delivery.token_expires_at,
                    self._secret,
                )
                await self._email.send_email_verification(
                    recipient=delivery.recipient,
                    display_name=delivery.display_name,
                    workspace_name=delivery.workspace_name,
                    verification_url=f"{self._public_base_url}/#verify-email={token}",
                    expires_at=delivery.token_expires_at,
                )
            except (ConnectionError, OSError, TimeoutError):
                delay = min(3600, 2 ** min(delivery.attempts, 10) * 30)
                if delivery.attempts >= self._max_attempts:
                    delay = 86400
                await self._store.mark_email_failed(
                    delivery_id=delivery.delivery_id,
                    error_code="smtp_delivery_failed",
                    retry_at=datetime.now(UTC) + timedelta(seconds=delay),
                )
            else:
                await self._store.mark_email_delivered(
                    delivery_id=delivery.delivery_id, delivered_at=datetime.now(UTC)
                )
        return len(deliveries)

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._poll_seconds)
            except TimeoutError:
                continue


def verification_token_for_delivery(
    *, token_id: str, intent_id: str, expires_at: datetime, token_secret: str
) -> str:
    return _verification_token(token_id, intent_id, expires_at, token_secret.encode("utf-8"))


def _verification_token(token_id: str, intent_id: str, expires_at: datetime, secret: bytes) -> str:
    payload = f"{token_id}:{intent_id}:{int(expires_at.timestamp())}"
    signature = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).digest()
    return f"{token_id}.{base64.urlsafe_b64encode(signature).decode('ascii').rstrip('=')}"


def _new_verification_token(
    *, intent_id: str, expires_at: datetime, secret: bytes
) -> EmailVerificationToken:
    token_id = str(uuid4())
    raw = _verification_token(token_id, intent_id, expires_at, secret)
    token_hash = hmac.new(secret, f"verification:{raw}".encode(), hashlib.sha256).hexdigest()
    now = datetime.now(UTC)
    return EmailVerificationToken(
        token_id=token_id,
        intent_id=intent_id,
        token_hash=token_hash,
        expires_at=expires_at,
        created_at=now,
    )


def _new_code() -> str:
    value = int.from_bytes(secrets.token_bytes(16), "big")
    chars: list[str] = []
    for _ in range(26):
        value, remainder = divmod(value, 32)
        chars.append(_CODE_ALPHABET[remainder])
    return "".join(reversed(chars))


def _email_allowed(email: str, policy: TenantEnrollmentPolicy) -> bool:
    domain = email.rsplit("@", 1)[1]
    return not policy.allowed_email_domains or domain in policy.allowed_email_domains


def _normalize_domain(value: str) -> str:
    domain = value.strip().lstrip("@").casefold()
    if not domain or "." not in domain or any(char.isspace() for char in domain):
        raise ValueError("email domains must be valid hostnames")
    try:
        return domain.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("email domains must be valid hostnames") from exc


def _clean_label(value: str, field: str) -> str:
    result = " ".join(value.split())
    if not 1 <= len(result) <= 200 or any(ord(char) < 32 for char in result):
        raise ValueError(f"{field} must contain between 1 and 200 characters")
    return result


def _clean_id(value: str, field: str) -> str:
    result = value.strip().casefold()
    if (
        not result
        or len(result) > 100
        or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for char in result)
    ):
        raise ValueError(f"{field} is invalid")
    return result


def _require_platform(platform_roles: frozenset[str]) -> None:
    if not platform_roles.intersection(_PLATFORM_ROLES):
        raise PermissionError("platform administration permission is required")


__all__ = [
    "EnrollmentAccessError",
    "EnrollmentConflictError",
    "EnrollmentRateLimitedError",
    "SelfServiceEnrollmentEmailWorker",
    "SelfServiceTenantProvisioningService",
    "verification_token_for_delivery",
]
