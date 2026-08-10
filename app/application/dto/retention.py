from dataclasses import dataclass
from datetime import datetime

from app.domain.models.retention import RetentionCounts


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    policy_version: str
    admin_session_days: int
    expired_memory_grace_days: int
    support_operation_days: int
    audit_event_days: int


@dataclass(frozen=True, slots=True)
class PreviewRetentionCommand:
    tenant_id: str
    policy: RetentionPolicy
    evaluated_at: datetime


@dataclass(frozen=True, slots=True)
class ExecuteRetentionCommand:
    tenant_id: str
    policy: RetentionPolicy
    evaluated_at: datetime
    run_key: str
    approval_reference: str
    actor_subject_id: str
    correlation_id: str


@dataclass(frozen=True, slots=True)
class RetentionResult:
    tenant_id: str
    policy_version: str
    counts: RetentionCounts
    executed: bool
