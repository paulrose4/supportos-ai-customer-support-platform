from dataclasses import dataclass

from app.domain.ports import KnowledgeControlPlaneSiteAudit, KnowledgeIndexSiteAudit


@dataclass(frozen=True, slots=True)
class AuditKnowledgeConsistencyQuery:
    tenant_id: str
    site_id: str
    baseline_publication_id: str | None = None


@dataclass(frozen=True, slots=True)
class KnowledgeConsistencyAuditResult:
    evaluated_at: str
    consistent: bool
    retrieval_ready: bool
    baseline_projection_complete: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    control_plane: KnowledgeControlPlaneSiteAudit
    vector_index: KnowledgeIndexSiteAudit
