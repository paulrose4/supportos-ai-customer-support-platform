from pydantic import BaseModel, Field


class AdminLoginRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=100)
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=500)


class AdminPasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=500)
    new_password: str = Field(min_length=12, max_length=500)


class EmailLoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=200)


class InvitationRegistrationRequest(BaseModel):
    invitation_token: str = Field(min_length=32, max_length=200)
    display_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=12, max_length=200)


class InvitationPreviewRequest(BaseModel):
    invitation_token: str = Field(min_length=32, max_length=200)


class PasswordResetRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class PasswordResetCompletionRequest(BaseModel):
    reset_token: str = Field(min_length=32, max_length=200)
    new_password: str = Field(min_length=12, max_length=200)


class AdminUserResponse(BaseModel):
    user_id: str
    tenant_id: str
    username: str
    display_name: str
    roles: list[str]
    scopes: list[str]
    status: str = "active"
    created_at: str | None = None
    updated_at: str | None = None
    authentication_method: str = "local"
    platform_roles: list[str] = Field(default_factory=list)


class AdminSessionResponse(BaseModel):
    user: AdminUserResponse
    expires_at: str | None = None


class AdminSessionItemResponse(BaseModel):
    session_id: str
    created_at: str
    last_seen_at: str
    expires_at: str
    revoked_at: str | None
    source_fingerprint_prefix: str
    is_current: bool


class AdminSessionListResponse(BaseModel):
    items: list[AdminSessionItemResponse] = Field(default_factory=list)


class AdminSessionRevocationResponse(BaseModel):
    session_id: str
    revoked: bool
    changed: bool
    was_current: bool


class AdminUserCreateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=12, max_length=200)
    roles: list[str] = Field(min_length=1, max_length=5)


class AdminUserUpdateRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=200)
    roles: list[str] = Field(min_length=1, max_length=5)
    status: str = Field(pattern="^(active|disabled)$")


class AdminUserPasswordResetRequest(BaseModel):
    new_password: str = Field(min_length=12, max_length=200)


class AdminUserListResponse(BaseModel):
    items: list[AdminUserResponse] = Field(default_factory=list)


class ExternalLoginProviderResponse(BaseModel):
    provider: str
    start_url: str


class ExternalLoginProviderListResponse(BaseModel):
    providers: list[ExternalLoginProviderResponse] = Field(default_factory=list)
    legacy_login_enabled: bool
    email_login_enabled: bool = False
    invite_registration_enabled: bool = False
    self_service_signup_enabled: bool = False
    password_reset_enabled: bool = False


class EmailAuthenticationResponse(BaseModel):
    user: AdminUserResponse | None = None
    expires_at: str | None = None
    workspace_selection_required: bool = False


class InvitationPreviewResponse(BaseModel):
    tenant_name: str
    email: str
    roles: list[str]
    expires_at: str


class WorkspaceResponse(BaseModel):
    tenant_id: str
    name: str
    roles: list[str]
    active: bool = False


class WorkspaceListResponse(BaseModel):
    items: list[WorkspaceResponse] = Field(default_factory=list)


class SelectWorkspaceRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=100)
