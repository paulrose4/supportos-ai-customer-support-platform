import logging

from app.domain.models import KnowledgeEvidence
from app.domain.ports import KnowledgeControlPlanePort, KnowledgeQuery, KnowledgeRetrieverPort

logger = logging.getLogger(__name__)


class SitePublicationGatedKnowledgeRetriever:
    """Prevent retrieval while a site's knowledge projection is being switched.

    The control-plane check is deliberately outside the Qdrant adapter. It
    keeps the infrastructure adapter reusable for indexing and read-only
    audits, while the online application path gets a fail-closed boundary.
    Any control-plane failure is treated as unavailable evidence.
    """

    def __init__(
        self,
        delegate: KnowledgeRetrieverPort,
        control_plane: KnowledgeControlPlanePort,
    ) -> None:
        self._delegate = delegate
        self._control_plane = control_plane

    async def search(self, query: KnowledgeQuery) -> list[KnowledgeEvidence]:
        site_id = str(query.filters.get("site_id") or "").strip()
        if not site_id:
            return await self._delegate.search(query)
        try:
            state = await self._control_plane.get_site_publication_state(
                tenant_id=query.tenant_id,
                site_id=site_id,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "knowledge retrieval blocked because publication state is unavailable",
                extra={"tenant_id": query.tenant_id, "site_id": site_id},
            )
            return []
        if state.state != "active":
            logger.warning(
                "knowledge retrieval blocked by site publication state",
                extra={
                    "tenant_id": query.tenant_id,
                    "site_id": site_id,
                    "publication_state": state.state,
                    "active_publication_id": state.active_publication_id,
                    "pending_publication_id": state.pending_publication_id,
                    "error_code": state.error_code,
                },
            )
            return []
        return await self._delegate.search(query)
