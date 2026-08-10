from typing import Annotated, Literal

from pydantic import BaseModel, Field


class ManagedSiteCreateRequest(BaseModel):
    site_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    base_url: str = Field(default="", max_length=500)
    site_key: str | None = Field(default=None, min_length=32, max_length=256)
    primary_language: str = Field(default="en", pattern=r"^[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})*$")


class ManagedSiteUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    base_url: str = Field(default="", max_length=500)
    status: str = Field(pattern="^(active|disabled)$")
    primary_language: str = Field(pattern=r"^[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})*$")


class ManagedSiteKeyRotationRequest(BaseModel):
    site_key: str = Field(min_length=32, max_length=256)


class ManagedSiteResponse(BaseModel):
    site_id: str
    public_widget_id: str
    name: str
    base_url: str
    allowed_origins: list[str]
    widget_daily_message_limit: int
    primary_language: str
    install_code: str
    status: str
    credential_key_prefix: str | None
    credential_status: str | None
    created_at: str
    updated_at: str
    verification_status: str
    verification_method: str | None = None
    verification_token_prefix: str | None = None
    verification_expires_at: str | None = None
    verified_at: str | None = None


class SiteVerificationChallengeResponse(BaseModel):
    site_id: str
    method: str
    dns_name: str
    dns_value: str
    script_path: str
    script_value: str
    expires_at: str
    verification_status: str


class SiteVerificationChallengeRequest(BaseModel):
    method: str = Field(pattern="^(dns_txt|script)$")


class SiteVerificationRequest(BaseModel):
    method: str = Field(pattern="^(dns_txt|script)$")


class ManagedSiteListResponse(BaseModel):
    items: list[ManagedSiteResponse] = Field(default_factory=list)


class SiteWebSourceUpdateRequest(BaseModel):
    discovery_mode: Literal["auto", "hybrid", "manual"]
    explicit_sitemap_urls: list[Annotated[str, Field(min_length=1, max_length=2048)]] = Field(
        default_factory=list, max_length=10
    )
    expected_config_version: int = Field(ge=0)


class SiteWebSourceResponse(BaseModel):
    site_id: str
    discovery_mode: Literal["auto", "hybrid", "manual"]
    explicit_sitemap_urls: list[str] = Field(default_factory=list)
    allowed_sitemap_origins: list[str] = Field(default_factory=list)
    config_version: int = Field(ge=0)
    validation_status: Literal["unvalidated", "valid", "invalid"]
    validated_at: str | None = None
    updated_by: str | None = None
    updated_at: str | None = None
