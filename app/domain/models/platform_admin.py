from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class PlatformActivity:
    event_type: str
    actor_subject_id: str | None
    resource_type: str
    resource_id: str
    details: dict[str, object]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PlatformSummary:
    active_workspace_count: int
    user_count: int
    pending_onboarding_count: int
    attention_count: int
    expiring_code_count: int
    failed_email_count: int
    orphan_workspace_count: int
    recent_activity: tuple[PlatformActivity, ...]


@dataclass(frozen=True, slots=True)
class PlatformTenantRecord:
    tenant_id: str
    name: str
    status: str
    owner_email: str | None
    member_count: int
    disabled_member_count: int
    site_count: int
    site_limit: int | None
    plan_id: str | None
    subscription_status: str | None
    created_at: datetime
    updated_at: datetime
    last_activity_at: datetime | None
    disabled_site_count: int = 0
    unverified_site_count: int = 0
    site_quota_used: int = 0
    owner_names: tuple[str, ...] = ()
    owner_emails: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PlatformSiteRecord:
    """Sanitized cross-workspace site directory row for platform administration."""

    tenant_id: str
    tenant_name: str
    site_id: str
    name: str
    base_url: str
    status: str
    verification_status: str
    knowledge_publication_state: str
    manager_names: tuple[str, ...]
    manager_emails: tuple[str, ...]
    created_at: datetime
    updated_at: datetime
    # Kept in the sanitized control-plane model so the application can derive
    # an expired pending challenge without exposing the timestamp in the API.
    verification_expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PlatformUserRecord:
    user_id: str
    display_name: str
    email: str | None
    status: str
    workspace_count: int
    workspace_names: tuple[str, ...]
    disabled_workspace_count: int
    disabled_workspace_names: tuple[str, ...]
    platform_roles: tuple[str, ...]
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PlatformMembershipRecord:
    membership_id: str
    tenant_id: str
    user_id: str
    display_name: str
    email: str | None
    roles: tuple[str, ...]
    status: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PlatformOnboardingSourceRecord:
    code_id: str
    policy_id: str
    target_email: str
    code_status: str
    expires_at: datetime
    created_by: str
    created_by_name: str | None
    created_at: datetime
    intent_status: str | None
    workspace_name: str | None
    proposed_tenant_id: str | None
    intent_expires_at: datetime | None
    completed_at: datetime | None
    email_status: str | None
    email_attempts: int | None
    email_sent_at: datetime | None
    email_last_error: str | None
