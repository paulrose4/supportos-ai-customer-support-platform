from pydantic import BaseModel, Field


class SelfServiceSignupRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=200)
    display_name: str = Field(min_length=1, max_length=200)
    workspace_name: str = Field(min_length=1, max_length=200)
    enterprise_code: str = Field(min_length=20, max_length=100)


class VerifySelfServiceEmailRequest(BaseModel):
    verification_token: str = Field(min_length=40, max_length=300)


class ResendSelfServiceVerificationRequest(BaseModel):
    status_token: str = Field(min_length=20, max_length=300)


class SelfServiceSignupResponse(BaseModel):
    status: str
    status_token: str
    expires_at: str


class VerifySelfServiceEmailResponse(BaseModel):
    status: str
    tenant_id: str
    workspace_name: str


class SelfServiceSignupStatusResponse(BaseModel):
    status: str
    expires_at: str
    tenant_id: str | None = None
    workspace_name: str


class CreateEnrollmentAuthorityRequest(BaseModel):
    authority_id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    max_active_tenants: int = Field(ge=1, le=1_000_000)
    max_total_tenants: int = Field(ge=1, le=1_000_000)


class CreateEnrollmentPolicyRequest(BaseModel):
    policy_id: str = Field(min_length=1, max_length=100)
    authority_id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    allowed_email_domains: list[str] = Field(min_length=1, max_length=100)
    require_exact_email_binding: bool = True
    default_plan_id: str = Field(default="trial", min_length=1, max_length=80)
    require_mfa: bool = False
    site_limit: int = Field(default=1, ge=1, le=100)


class IssueEnrollmentCodeRequest(BaseModel):
    policy_id: str = Field(min_length=1, max_length=100)
    target_email: str = Field(min_length=3, max_length=320)
    expires_in_hours: int = Field(default=24, ge=1, le=168)


class IssueWorkspaceOnboardingCodeRequest(BaseModel):
    target_email: str = Field(min_length=3, max_length=320)
    expires_in_hours: int = Field(default=72, ge=1, le=168)
    site_limit: int = Field(default=1, ge=1, le=100)


class EnrollmentCodeResponse(BaseModel):
    code_id: str
    policy_id: str
    code_prefix: str
    status: str
    target_email: str
    expires_at: str
    consumed_at: str | None = None


class IssueEnrollmentCodeResponse(BaseModel):
    code: EnrollmentCodeResponse
    enrollment_code: str


class IssueWorkspaceOnboardingCodeResponse(BaseModel):
    code: EnrollmentCodeResponse
    enrollment_code: str
    signup_url: str


class EnrollmentCodeListResponse(BaseModel):
    items: list[EnrollmentCodeResponse] = Field(default_factory=list)
