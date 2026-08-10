from datetime import datetime
from typing import Protocol

from app.domain.models.onboarding import (
    EmailVerificationToken,
    EnrollmentAuthority,
    EnrollmentCompletion,
    EnrollmentEmailDelivery,
    EnrollmentIntent,
    TenantEnrollmentCode,
    TenantEnrollmentPolicy,
)


class EnrollmentRateLimitPort(Protocol):
    async def consume(
        self,
        *,
        bucket: str,
        source_key: str,
        limit: int,
        window_seconds: int,
        occurred_at: datetime,
    ) -> bool: ...


class SelfServiceOnboardingStorePort(Protocol):
    async def expire_enrollment_intents(self, *, expired_at: datetime) -> int: ...

    async def create_authority(
        self,
        authority: EnrollmentAuthority,
        *,
        correlation_id: str,
    ) -> EnrollmentAuthority: ...

    async def create_policy(
        self,
        policy: TenantEnrollmentPolicy,
        *,
        correlation_id: str,
    ) -> TenantEnrollmentPolicy: ...

    async def ensure_authority_and_policy(
        self,
        authority: EnrollmentAuthority,
        policy: TenantEnrollmentPolicy,
        *,
        correlation_id: str,
    ) -> TenantEnrollmentPolicy: ...

    async def create_code(
        self,
        code: TenantEnrollmentCode,
        *,
        correlation_id: str,
    ) -> TenantEnrollmentCode: ...

    async def get_code_by_digest(
        self,
        *,
        code_digest: str,
        checked_at: datetime,
    ) -> TenantEnrollmentCode | None: ...

    async def get_policy(self, *, policy_id: str) -> TenantEnrollmentPolicy | None: ...

    async def list_codes(self, *, policy_id: str) -> list[TenantEnrollmentCode]: ...

    async def revoke_code(
        self,
        *,
        code_id: str,
        actor_subject_id: str,
        correlation_id: str,
        revoked_at: datetime,
    ) -> TenantEnrollmentCode | None: ...

    async def start_enrollment(
        self,
        *,
        code_digest: str,
        intent: EnrollmentIntent,
        verification: EmailVerificationToken,
        delivery: EnrollmentEmailDelivery,
        correlation_id: str,
        occurred_at: datetime,
    ) -> EnrollmentIntent: ...

    async def resend_verification(
        self,
        *,
        status_token_hash: str,
        verification: EmailVerificationToken,
        delivery: EnrollmentEmailDelivery,
        correlation_id: str,
        occurred_at: datetime,
    ) -> EnrollmentIntent | None: ...

    async def get_enrollment_status(
        self,
        *,
        status_token_hash: str,
        checked_at: datetime,
    ) -> EnrollmentIntent | None: ...

    async def complete_enrollment(
        self,
        *,
        verification_token_hash: str,
        correlation_id: str,
        completed_at: datetime,
    ) -> EnrollmentCompletion | None: ...

    async def claim_email_deliveries(
        self,
        *,
        claimed_at: datetime,
        lease_seconds: int,
        limit: int,
    ) -> list[EnrollmentEmailDelivery]: ...

    async def mark_email_delivered(
        self,
        *,
        delivery_id: str,
        delivered_at: datetime,
    ) -> None: ...

    async def mark_email_failed(
        self,
        *,
        delivery_id: str,
        error_code: str,
        retry_at: datetime,
    ) -> None: ...
