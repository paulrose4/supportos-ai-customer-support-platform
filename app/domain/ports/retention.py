from datetime import datetime
from typing import Protocol

from app.domain.models.retention import RetentionCounts


class RetentionStorePort(Protocol):
    async def preview(
        self,
        *,
        tenant_id: str,
        session_cutoff: datetime,
        memory_cutoff: datetime,
        operation_cutoff: datetime,
        audit_cutoff: datetime,
    ) -> RetentionCounts: ...

    async def execute(
        self,
        *,
        tenant_id: str,
        session_cutoff: datetime,
        memory_cutoff: datetime,
        operation_cutoff: datetime,
        audit_cutoff: datetime,
        run_key: str,
        policy_version: str,
        approval_reference: str,
        actor_subject_id: str,
        correlation_id: str,
        executed_at: datetime,
    ) -> RetentionCounts: ...
