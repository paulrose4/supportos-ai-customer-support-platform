from pydantic import BaseModel, Field


class AuditEventResponse(BaseModel):
    event_id: str
    event_type: str
    actor_subject_id: str | None
    correlation_id: str | None
    trace_id: str | None
    resource_type: str
    resource_id: str
    details: dict[str, object] = Field(default_factory=dict)
    created_at: str


class AuditEventListResponse(BaseModel):
    items: list[AuditEventResponse] = Field(default_factory=list)
    next_cursor: str | None = None
