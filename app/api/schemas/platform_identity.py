from pydantic import BaseModel, Field


class PlatformTenantResponse(BaseModel):
    tenant_id: str
    name: str
    status: str
    created_at: str
    updated_at: str
    owner_email: str | None = None
    owner_names: list[str] = Field(default_factory=list)
    owner_emails: list[str] = Field(default_factory=list)
    member_count: int = 0
    disabled_member_count: int = 0
    site_count: int = 0
    disabled_site_count: int = 0
    unverified_site_count: int = 0
    site_quota_used: int = 0
    site_limit: int | None = None
    plan_id: str | None = None
    subscription_status: str | None = None
    last_activity_at: str | None = None


class PlatformTenantListResponse(BaseModel):
    items: list[PlatformTenantResponse] = Field(default_factory=list)
    total: int = 0
    next_cursor: str | None = None


class PlatformSiteResponse(BaseModel):
    tenant_id: str
    tenant_name: str
    site_id: str
    name: str
    base_url: str
    status: str
    verification_status: str
    knowledge_publication_state: str
    manager_names: list[str] = Field(default_factory=list)
    manager_emails: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str


class PlatformSiteListResponse(BaseModel):
    items: list[PlatformSiteResponse] = Field(default_factory=list)
    total: int = 0
    next_cursor: str | None = None


class CreatePlatformTenantRequest(BaseModel):
    tenant_id: str = Field(min_length=3, max_length=100)
    name: str = Field(min_length=1, max_length=200)


class PlatformUserResponse(BaseModel):
    user_id: str
    display_name: str
    email: str | None = None
    status: str
    created_at: str
    updated_at: str
    workspace_count: int = 0
    workspace_names: list[str] = Field(default_factory=list)
    disabled_workspace_count: int = 0
    disabled_workspace_names: list[str] = Field(default_factory=list)
    platform_roles: list[str] = Field(default_factory=list)
    last_login_at: str | None = None


class PlatformUserListResponse(BaseModel):
    items: list[PlatformUserResponse] = Field(default_factory=list)
    total: int = 0
    next_cursor: str | None = None


class PlatformActivityResponse(BaseModel):
    event_type: str
    actor_subject_id: str | None = None
    resource_type: str
    resource_id: str
    details: dict[str, object] = Field(default_factory=dict)
    created_at: str


class PlatformSummaryResponse(BaseModel):
    active_workspace_count: int
    user_count: int
    pending_onboarding_count: int
    attention_count: int
    expiring_code_count: int
    failed_email_count: int
    orphan_workspace_count: int
    recent_activity: list[PlatformActivityResponse] = Field(default_factory=list)


class PlatformMembershipListResponse(BaseModel):
    items: list["TenantMembershipResponse"] = Field(default_factory=list)


class PlatformOnboardingRecordResponse(BaseModel):
    code_id: str
    target_email: str
    status: str
    expires_at: str
    created_by: str
    created_by_name: str | None = None
    created_at: str
    workspace_name: str | None = None
    tenant_id: str | None = None
    email_status: str | None = None
    email_attempts: int = 0
    email_sent_at: str | None = None
    email_last_error: str | None = None


class PlatformOnboardingRecordListResponse(BaseModel):
    items: list[PlatformOnboardingRecordResponse] = Field(default_factory=list)
    total: int = 0
    next_cursor: str | None = None


class UpsertTenantMembershipRequest(BaseModel):
    roles: list[str] = Field(min_length=1, max_length=5)
    status: str = Field(pattern="^(active|disabled)$")


class TenantMembershipResponse(BaseModel):
    membership_id: str
    tenant_id: str
    tenant_name: str
    user_id: str
    display_name: str | None = None
    email: str | None = None
    roles: list[str]
    scopes: list[str]
    status: str


class AssignPlatformRoleRequest(BaseModel):
    role: str = Field(pattern="^(platform_owner|platform_operator|platform_auditor)$")


class CreateTenantInvitationRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    roles: list[str] = Field(min_length=1, max_length=5)
    expires_in_hours: int = Field(default=72, ge=1, le=168)


class TenantInvitationResponse(BaseModel):
    invitation_id: str
    tenant_id: str
    tenant_name: str
    email: str
    roles: list[str]
    status: str
    expires_at: str
    created_at: str
    redeemed_at: str | None = None
    revoked_at: str | None = None


class CreatedTenantInvitationResponse(TenantInvitationResponse):
    invitation_url: str
    email_sent: bool


class TenantInvitationListResponse(BaseModel):
    items: list[TenantInvitationResponse] = Field(default_factory=list)
