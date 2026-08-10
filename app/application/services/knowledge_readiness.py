from datetime import datetime

from app.application.dto.knowledge_readiness import (
    GetSiteKnowledgeReadinessQuery,
    SiteKnowledgeReadinessResult,
)
from app.application.tenant_context import global_knowledge_scope
from app.domain.ports import KnowledgeControlPlanePort, ProductCatalogPort


class SiteKnowledgeReadinessService:
    def __init__(
        self,
        *,
        catalog: ProductCatalogPort,
        control_plane: KnowledgeControlPlanePort,
    ) -> None:
        self._catalog = catalog
        self._control_plane = control_plane

    async def get(
        self,
        query: GetSiteKnowledgeReadinessQuery,
    ) -> SiteKnowledgeReadinessResult:
        if not query.principal.scopes.intersection({"knowledge:read", "knowledge:sync"}):
            raise PermissionError("knowledge readiness requires knowledge access")
        documents = await self._control_plane.list_active_site_web_documents(
            tenant_id=query.principal.tenant_id,
            site_id=query.site_id,
        )
        catalog = await self._catalog.get_active_summary(
            tenant_id=query.principal.tenant_id,
            site_id=query.site_id,
        )
        policy_ready = any(_is_policy(item.content_kind, item.topics) for item in documents)
        with global_knowledge_scope():
            care_ready = await self._control_plane.has_active_approved_care_sop(query.language)
        catalog_ready = bool(catalog.snapshot_id and catalog.product_count > 0)
        blocking = []
        if not catalog_ready:
            blocking.append("active_product_catalog_missing")
        if not policy_ready:
            blocking.append("published_policy_knowledge_missing")
        if not care_ready:
            blocking.append(
                "published_care_knowledge_missing"
                if query.language.casefold() in {"", "en", "zh", "zh-cn"}
                else "published_care_knowledge_missing_for_site_language"
            )
        timestamps = [
            value
            for value in (
                catalog.completed_at,
                *(_parse_timestamp(item.activated_at) for item in documents),
            )
            if value is not None
        ]
        return SiteKnowledgeReadinessResult(
            site_id=query.site_id,
            ready=not blocking,
            catalog_ready=catalog_ready,
            policy_ready=policy_ready,
            care_ready=care_ready,
            active_product_count=catalog.product_count,
            active_document_count=len(documents),
            active_snapshot_id=catalog.snapshot_id,
            last_successful_sync_at=max(timestamps) if timestamps else None,
            blocking_reasons=tuple(blocking),
        )


def _is_policy(content_kind: str, topics: tuple[str, ...]) -> bool:
    values = {content_kind.casefold(), *(item.casefold() for item in topics)}
    # ``delivery`` is retained as a backwards-compatible alias for the
    # canonical shipping topic used by newer integrations.
    return bool(
        values & {"policy", "shipping", "delivery", "returns", "privacy", "payment", "warranty"}
    )


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
