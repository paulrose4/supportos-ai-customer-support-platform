from pydantic import BaseModel, ConfigDict, Field, model_validator


class PublicWidgetBootstrapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    public_widget_id: str = Field(min_length=16, max_length=80)
    resume_token: str | None = Field(default=None, min_length=32, max_length=512)
    session_token: str | None = Field(default=None, min_length=32, max_length=2048)


class PublicWidgetChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_token: str = Field(min_length=32, max_length=2048)
    request_id: str = Field(min_length=1, max_length=100)
    conversation_id: str | None = Field(default=None, max_length=100)
    message: str = Field(min_length=1, max_length=10_000)
    page_path: str = Field(default="/", min_length=1, max_length=500)
    dwell_seconds: int = Field(default=0, ge=0, le=86400)


class PublicWidgetMessagesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_token: str = Field(min_length=32, max_length=2048)
    conversation_id: str = Field(min_length=1, max_length=100)
    after_cursor: str | None = Field(default=None, max_length=1024)
    limit: int = Field(default=20, ge=1, le=50)


class PublicWidgetConversationStateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_token: str = Field(min_length=32, max_length=2048)
    conversation_id: str = Field(min_length=1, max_length=100)


class PublicWidgetEventStreamRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_token: str = Field(min_length=32, max_length=2048)
    conversation_id: str = Field(min_length=1, max_length=100)


class PublicWidgetPresenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_token: str | None = Field(default=None, min_length=32, max_length=2048)
    presence_token: str | None = Field(default=None, min_length=32, max_length=2048)
    public_widget_id: str | None = Field(default=None, min_length=16, max_length=80)
    visitor_id: str = Field(min_length=1, max_length=100)
    conversation_id: str | None = Field(default=None, max_length=100)
    page_path: str = Field(default="/", min_length=1, max_length=500)
    # Accepted for forward compatibility but treated as browser advisory data;
    # the public route never promotes it to trusted scoring taxonomy.
    page_kind: str | None = Field(
        default=None,
        pattern="^(home|category|product|comparison|shipping|payment|pricing|cart|checkout|order_confirmation|support|content|unknown)$",
    )
    page_title: str | None = Field(default=None, max_length=200)
    referrer: str | None = Field(default=None, max_length=1000)
    language: str | None = Field(default=None, max_length=35)
    timezone: str | None = Field(default=None, max_length=100)
    event: str = Field(default="heartbeat", pattern="^(enter|heartbeat)$")
    page_view_id: str | None = Field(default=None, min_length=1, max_length=100)
    widget_state: str | None = Field(default=None, pattern="^(closed|open)$")
    presence_source: str | None = Field(default=None, pattern="^(page_load|widget)$")
    runtime_version: str | None = Field(default=None, min_length=1, max_length=100)
    config_version: str | None = Field(default=None, min_length=1, max_length=100)
    connector_type: str | None = Field(
        default=None,
        pattern="^(public|wordpress|static_php|cloudflare_worker|legacy)$",
    )
    connector_version: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_credential(self) -> "PublicWidgetPresenceRequest":
        credentials = (self.session_token, self.presence_token, self.public_widget_id)
        if sum(value is not None for value in credentials) != 1:
            raise ValueError("exactly one public widget presence credential is required")
        return self
