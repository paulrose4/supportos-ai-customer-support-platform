from app.domain.models import KnowledgeEvidence
from app.domain.ports import GLOBAL_KNOWLEDGE_PARTITION


def has_sufficient_knowledge(evidence: list[KnowledgeEvidence], minimum_score: float = 0.5) -> bool:
    return bool(evidence) and max(item.score for item in evidence) >= minimum_score


def assert_single_tenant(evidence: list[KnowledgeEvidence], tenant_id: str) -> None:
    allowed_tenants = {tenant_id, GLOBAL_KNOWLEDGE_PARTITION}
    if any(item.metadata.get("tenant_id") not in allowed_tenants for item in evidence):
        raise PermissionError("cross-tenant evidence detected")
