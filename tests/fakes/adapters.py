from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import md5
from typing import Any
from uuid import uuid4

from app.domain.models import (
    AgentResponse,
    AuthenticatedPrincipal,
    ConversationRoutingState,
    ConversationStatus,
    ConversationTurn,
    HandoffRequest,
    HumanSupportMessage,
    KnowledgeEvidence,
    Order,
    OwnershipMode,
    ProductDataStatus,
    ProductSnapshot,
    ProductSnapshotActivation,
    SupportTicket,
    WebCrawlManifest,
    WebCrawlManifestItem,
    WebCrawlPageState,
)
from app.domain.ports import (
    ActiveProductCatalogSummary,
    ActiveWebDocumentSnapshot,
    ChatModelRequest,
    ChatModelResult,
    KnowledgeChunk,
    KnowledgeChunkRecord,
    KnowledgeQuery,
    KnowledgeSitePublicationState,
    KnowledgeVersionDraft,
    KnowledgeVersionSnapshot,
    ProductIdentityConflictError,
    ProductLookup,
    SparseEmbedding,
)
from app.domain.rules import product_identity_conflicts


class InMemoryHandoffAdapter:
    def __init__(self) -> None:
        self.requests: dict[tuple[str, str], HandoffRequest] = {}

    async def create(self, request: HandoffRequest) -> HandoffRequest:
        key = (request.tenant_id, request.idempotency_key)
        if key not in self.requests:
            self.requests[key] = replace(request, created_at=datetime.now(UTC))
        return self.requests[key]

    async def list_for_tenant(
        self,
        *,
        tenant_id: str,
        status: str | None,
        limit: int,
    ) -> list[HandoffRequest]:
        requests = [
            request
            for request in self.requests.values()
            if request.tenant_id == tenant_id and (status is None or request.status.value == status)
        ]
        requests.sort(key=lambda item: item.created_at or datetime.min.replace(tzinfo=UTC))
        return requests[:limit]


class InMemoryConversationPersistenceAdapter:
    def __init__(self) -> None:
        self.exchanges: list[tuple[AuthenticatedPrincipal, str, AgentResponse]] = []
        self.exchange_metadata: list[dict[str, Any]] = []
        self.routing_states: dict[str, ConversationRoutingState] = {}
        self.human_messages: dict[str, tuple[HumanSupportMessage, ...]] = {}

    async def load_routing_state(
        self,
        *,
        principal: AuthenticatedPrincipal,
        conversation_id: str,
    ) -> ConversationRoutingState | None:
        explicit = self.routing_states.get(conversation_id)
        if explicit is not None:
            return explicit
        for stored_principal, _user_message, response in self.exchanges:
            if response.conversation_id != conversation_id:
                continue
            if stored_principal.tenant_id != principal.tenant_id:
                continue
            if stored_principal.site_id != principal.site_id:
                continue
            if principal.authentication_method in {
                "public_presence_token",
                "public_widget_token",
            } and (stored_principal.visitor_session_id != principal.visitor_session_id):
                continue
            return ConversationRoutingState(
                conversation_id=conversation_id,
                status=ConversationStatus.OPEN,
                ownership_mode=OwnershipMode.AI,
            )
        return None

    async def authorize_conversation(
        self,
        *,
        principal: AuthenticatedPrincipal,
        conversation_id: str,
    ) -> bool:
        if principal.authentication_method in {"widget_site_key", "wordpress_site_key"}:
            # Site-key connector tests model a trusted server-side association.
            return True
        return (
            await self.load_routing_state(
                principal=principal,
                conversation_id=conversation_id,
            )
            is not None
        )

    async def update_visitor_context(
        self,
        *,
        principal: AuthenticatedPrincipal,
        conversation_id: str,
        ip_address: str | None,
        country_code: str | None,
    ) -> None:
        del ip_address, country_code
        if not await self.authorize_conversation(
            principal=principal,
            conversation_id=conversation_id,
        ):
            raise PermissionError("conversation is not available to the principal")

    async def load_human_messages(
        self,
        *,
        principal: AuthenticatedPrincipal,
        conversation_id: str,
        limit: int,
        after_id: int = 0,
    ) -> tuple[HumanSupportMessage, ...]:
        del principal
        messages = self.human_messages.get(conversation_id, ())
        if after_id > 0:
            return tuple(item for item in messages if item.cursor > after_id)[:limit]
        return messages[-limit:]

    async def persist_exchange(
        self,
        *,
        principal: AuthenticatedPrincipal,
        user_message: str,
        response: AgentResponse,
        **metadata: Any,
    ) -> None:
        self.exchanges.append((principal, user_message, response))
        self.exchange_metadata.append(metadata)

    async def load_recent(
        self,
        *,
        principal: AuthenticatedPrincipal,
        conversation_id: str,
        limit: int,
    ) -> tuple[ConversationTurn, ...]:
        turns: list[ConversationTurn] = []
        for stored_principal, user_message, response in self.exchanges:
            if (
                stored_principal.tenant_id != principal.tenant_id
                or stored_principal.site_id != principal.site_id
                or response.conversation_id != conversation_id
            ):
                continue
            turns.extend(
                (
                    ConversationTurn(role="user", content=user_message),
                    ConversationTurn(role="assistant", content=response.message),
                )
            )
        return tuple(turns[-limit:])


class InMemoryKnowledgeRetriever:
    def __init__(self, evidence: Sequence[KnowledgeEvidence] = ()) -> None:
        self.evidence = list(evidence)
        self.queries: list[KnowledgeQuery] = []
        self.error: Exception | None = None

    async def search(self, query: KnowledgeQuery) -> list[KnowledgeEvidence]:
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        return [
            item
            for item in self.evidence
            if item.metadata.get("tenant_id") in {query.tenant_id, "__global__"}
        ]


class InMemoryWebCrawlManifestStore:
    def __init__(self, manifests: Sequence[WebCrawlManifest] = ()) -> None:
        self.manifests = {
            (item.tenant_id, item.site_id, item.manifest_id): item for item in manifests
        }
        self.page_states: dict[tuple[str, str, str], WebCrawlPageState] = {
            (manifest.tenant_id, manifest.site_id, item.url): WebCrawlPageState(
                tenant_id=manifest.tenant_id,
                site_id=manifest.site_id,
                url=item.url,
                document_id=item.document_id,
                version_id=item.version_id,
                canonical_url=item.canonical_url,
                final_url=item.final_url or item.canonical_url,
                etag=item.etag,
                last_modified=item.response_last_modified,
                product_key=item.product_key,
                artifact_status=item.artifact_status,
                validated_at=item.validated_at,
            )
            for manifest in manifests
            for item in manifest.items
            if item.document_id
            and item.version_id
            and item.canonical_url
            and item.artifact_status == "published"
        }

    async def save(self, manifest: WebCrawlManifest) -> WebCrawlManifest:
        latest_version = max(
            (
                item.version
                for item in self.manifests.values()
                if (item.tenant_id, item.site_id) == (manifest.tenant_id, manifest.site_id)
            ),
            default=0,
        )
        persisted = replace(
            manifest,
            version=latest_version + 1,
            item_count=manifest.url_count,
            item_kind_counts=tuple(sorted(manifest.content_kind_counts.items())),
        )
        self.manifests[(manifest.tenant_id, manifest.site_id, manifest.manifest_id)] = persisted
        return persisted

    async def get(
        self,
        *,
        tenant_id: str,
        site_id: str,
        manifest_id: str,
    ) -> WebCrawlManifest | None:
        return self.manifests.get((tenant_id, site_id, manifest_id))

    async def get_latest(
        self,
        *,
        tenant_id: str,
        site_id: str,
    ) -> WebCrawlManifest | None:
        candidates = [
            item
            for (stored_tenant, stored_site, _manifest_id), item in self.manifests.items()
            if (stored_tenant, stored_site) == (tenant_id, site_id)
        ]
        return max(candidates, key=lambda item: item.created_at, default=None)

    async def get_metadata(
        self,
        *,
        tenant_id: str,
        site_id: str,
        manifest_id: str,
    ) -> WebCrawlManifest | None:
        manifest = await self.get(
            tenant_id=tenant_id,
            site_id=site_id,
            manifest_id=manifest_id,
        )
        if manifest is None:
            return None
        return replace(
            manifest,
            item_count=manifest.url_count,
            item_kind_counts=tuple(sorted(manifest.content_kind_counts.items())),
            items=(),
        )

    async def list_items(
        self,
        *,
        tenant_id: str,
        site_id: str,
        manifest_id: str,
        offset: int,
        limit: int,
        deterministic_sample: bool = False,
    ) -> tuple[WebCrawlManifestItem, ...]:
        manifest = self.manifests[(tenant_id, site_id, manifest_id)]
        ordering = (
            (lambda item: (md5(item.url.encode(), usedforsecurity=False).hexdigest(), item.url))
            if deterministic_sample
            else (lambda item: item.url)
        )
        return tuple(sorted(manifest.items, key=ordering))[offset : offset + limit]

    async def list_page_states(
        self,
        *,
        tenant_id: str,
        site_id: str,
    ) -> tuple[WebCrawlPageState, ...]:
        return tuple(
            state
            for (stored_tenant, stored_site, _url), state in self.page_states.items()
            if (stored_tenant, stored_site) == (tenant_id, site_id)
        )

    async def replace_page_states(
        self,
        *,
        tenant_id: str,
        site_id: str,
        states: tuple[WebCrawlPageState, ...],
    ) -> None:
        if any(state.tenant_id != tenant_id or state.site_id != site_id for state in states):
            raise ValueError("page states must belong to the requested tenant and site")
        self.page_states = {
            key: state for key, state in self.page_states.items() if key[:2] != (tenant_id, site_id)
        }
        for state in states:
            self.page_states[(state.tenant_id, state.site_id, state.url)] = state


class InMemoryKnowledgeIndexer:
    def __init__(self) -> None:
        self.initialized = False
        self.chunks: dict[str, KnowledgeChunk] = {}
        self.deleted_documents: list[tuple[str, str]] = []
        self.activated_sites: list[tuple[str, str, tuple[str, ...]]] = []
        self.discarded_snapshots: list[tuple[str, str, str]] = []

    async def initialize(self) -> None:
        self.initialized = True

    async def upsert(self, chunks: Sequence[KnowledgeChunk]) -> None:
        self.chunks.update({chunk.chunk_id: chunk for chunk in chunks})

    async def delete_document(self, *, tenant_id: str, document_id: str) -> None:
        self.deleted_documents.append((tenant_id, document_id))
        self.chunks = {
            key: chunk
            for key, chunk in self.chunks.items()
            if (chunk.tenant_id, chunk.document_id) != (tenant_id, document_id)
        }

    async def activate_document_version(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str | None,
    ) -> None:
        self.chunks = {
            key: replace(
                chunk,
                metadata={
                    **chunk.metadata,
                    "is_active": (
                        version_id is not None and chunk.version_id == version_id
                        if chunk.tenant_id == tenant_id and chunk.document_id == document_id
                        else chunk.metadata.get("is_active", True)
                    ),
                },
            )
            for key, chunk in self.chunks.items()
        }

    async def activate_site_versions(
        self,
        *,
        tenant_id: str,
        site_id: str,
        version_ids: Sequence[str],
        expected_point_count: int | None = None,
    ) -> None:
        del expected_point_count
        active = set(version_ids)
        self.activated_sites.append((tenant_id, site_id, tuple(version_ids)))
        self.chunks = {
            key: replace(
                chunk,
                metadata={
                    **chunk.metadata,
                    "is_active": (
                        chunk.version_id in active
                        if chunk.tenant_id == tenant_id
                        and chunk.metadata.get("site_id") == site_id
                        and chunk.metadata.get("source_type") == "website_html"
                        else chunk.metadata.get("is_active", True)
                    ),
                },
            )
            for key, chunk in self.chunks.items()
        }

    async def discard_snapshot(
        self,
        *,
        tenant_id: str,
        site_id: str,
        snapshot_id: str,
    ) -> None:
        self.discarded_snapshots.append((tenant_id, site_id, snapshot_id))
        self.chunks = {
            key: chunk
            for key, chunk in self.chunks.items()
            if not (
                chunk.tenant_id == tenant_id
                and chunk.metadata.get("site_id") == site_id
                and chunk.metadata.get("snapshot_id") == snapshot_id
                and chunk.metadata.get("is_active") is False
            )
        }

    async def discard_staged_document(self, **values: Any) -> None:
        tenant_id = values["tenant_id"]
        site_id = values["site_id"]
        snapshot_id = values["snapshot_id"]
        document_id = values["document_id"]
        self.chunks = {
            key: chunk
            for key, chunk in self.chunks.items()
            if not (
                chunk.tenant_id == tenant_id
                and chunk.document_id == document_id
                and chunk.metadata.get("site_id") == site_id
                and chunk.metadata.get("snapshot_id") == snapshot_id
                and chunk.metadata.get("is_active") is False
            )
        }

    async def count_snapshot_points(
        self,
        *,
        tenant_id: str,
        site_id: str,
        snapshot_id: str,
    ) -> int:
        return sum(
            chunk.tenant_id == tenant_id
            and chunk.metadata.get("site_id") == site_id
            and chunk.metadata.get("snapshot_id") == snapshot_id
            for chunk in self.chunks.values()
        )


def _fake_care_version_supports_language(metadata: dict[str, Any], language: str | None) -> bool:
    if (
        not str(metadata.get("care_pack_id") or "").strip()
        or not str(metadata.get("care_pack_version") or "").strip()
        or metadata.get("care_scope") != "company_global"
    ):
        return False
    if not language:
        return True
    requested = str(language).strip().replace("_", "-").casefold().split("-", 1)[0]
    declared = metadata.get("care_locales")
    if isinstance(declared, list) and declared:
        declared_bases = {
            str(value).strip().replace("_", "-").casefold().split("-", 1)[0] for value in declared
        }
        if requested not in declared_bases:
            return False
    steps = metadata.get("approved_steps", [])
    if metadata.get("category") == "product_care_sop":
        if not steps:
            return requested in {"en", "zh"} and not declared
        return bool(steps) and all(
            isinstance(step, dict)
            and isinstance(step.get("instructions"), dict)
            and any(
                str(key).strip().replace("_", "-").casefold().split("-", 1)[0] == requested
                and str(value or "").strip()
                for key, value in step["instructions"].items()
            )
            for step in steps
        )
    responses = metadata.get("approved_responses")
    return isinstance(responses, dict) and any(
        str(key).strip().replace("_", "-").casefold().split("-", 1)[0] == requested
        and str(value or "").strip()
        for key, value in responses.items()
    )


class InMemoryKnowledgeControlPlane:
    def __init__(self) -> None:
        self.versions: dict[tuple[str, str], KnowledgeVersionSnapshot] = {}
        self.version_snapshots: dict[tuple[str, str], KnowledgeVersionSnapshot] = {}
        self.staged: list[KnowledgeVersionDraft] = []
        self.indexed: list[tuple[str, str, str, tuple[str, ...]]] = []
        self.excluded: list[tuple[str, str, str]] = []
        self.conflicts: list[tuple[str, str, tuple[str, ...], int]] = []
        self.ingestion_rejections: list[tuple[str, str, str, str]] = []
        self.completed_jobs: list[dict[str, Any]] = []
        self.failed_jobs: list[dict[str, Any]] = []
        self.activated_sites: list[tuple[str, str, tuple[str, ...]]] = []
        self.publication_states: dict[tuple[str, str], KnowledgeSitePublicationState] = {}

    async def has_active_approved_care_sop(self, language: str | None = None) -> bool:
        drafts = {draft.version_id: draft for draft in self.staged}
        return any(
            stored_tenant == "__global__"
            and snapshot.index_status == "active"
            and (draft := drafts.get(snapshot.version_id)) is not None
            and draft.status == "published"
            and draft.authority_level >= 80
            and draft.metadata.get("category") == "product_care_sop"
            and draft.metadata.get("approval_status") == "approved"
            and bool(draft.metadata.get("approval_references"))
            and _fake_care_version_supports_language(draft.metadata, language)
            for (stored_tenant, _document_id), snapshot in self.versions.items()
        )

    async def list_active_site_web_documents(
        self, *, tenant_id: str, site_id: str
    ) -> tuple[ActiveWebDocumentSnapshot, ...]:
        drafts = {draft.version_id: draft for draft in self.staged}
        result: list[ActiveWebDocumentSnapshot] = []
        for (stored_tenant, document_id), snapshot in self.versions.items():
            if stored_tenant != tenant_id or snapshot.index_status != "active":
                continue
            draft = drafts.get(snapshot.version_id)
            if draft is None or draft.metadata.get("site_id") != site_id:
                continue
            metadata = draft.metadata
            canonical_url = str(metadata.get("canonical_url") or draft.source_path)
            product = metadata.get("product")
            product_identity = product if isinstance(product, dict) else {}
            product_key = str(
                product_identity.get("sku") or product_identity.get("mpn") or ""
            ).strip()
            result.append(
                ActiveWebDocumentSnapshot(
                    document_id=document_id,
                    version_id=snapshot.version_id,
                    canonical_url=canonical_url,
                    requested_url=str(metadata.get("requested_url") or canonical_url),
                    final_url=str(metadata.get("final_url") or canonical_url),
                    etag=str(metadata.get("etag") or "") or None,
                    last_modified=str(metadata.get("last_modified") or "") or None,
                    product_key=product_key.upper() or None,
                    content_kind=str(metadata.get("content_kind") or "general"),
                    topics=tuple(
                        str(value)
                        for value in metadata.get("content_topics", metadata.get("topics", []))
                    ),
                )
            )
        return tuple(result)

    async def list_active_site_version_ids(
        self, *, tenant_id: str, site_id: str
    ) -> tuple[str, ...]:
        drafts = {draft.version_id: draft for draft in self.staged}
        return tuple(
            snapshot.version_id
            for (stored_tenant, _document_id), snapshot in self.versions.items()
            if stored_tenant == tenant_id
            and snapshot.index_status == "active"
            and (draft := drafts.get(snapshot.version_id)) is not None
            and draft.metadata.get("site_id") == site_id
        )

    async def list_publication_version_ids(
        self,
        *,
        tenant_id: str,
        site_id: str,
        publication_id: str,
    ) -> tuple[str, ...]:
        return tuple(
            draft.version_id
            for draft in self.staged
            if draft.tenant_id == tenant_id
            and draft.metadata.get("site_id") == site_id
            and draft.metadata.get("snapshot_id") == publication_id
        )

    async def get_site_publication_state(
        self,
        *,
        tenant_id: str,
        site_id: str,
    ) -> KnowledgeSitePublicationState:
        return self.publication_states.get(
            (tenant_id, site_id),
            KnowledgeSitePublicationState(state="active"),
        )

    async def begin_site_publication_switch(
        self,
        *,
        tenant_id: str,
        site_id: str,
        publication_id: str,
    ) -> KnowledgeSitePublicationState:
        key = (tenant_id, site_id)
        current = await self.get_site_publication_state(tenant_id=tenant_id, site_id=site_id)
        if current.state == "switching":
            if current.pending_publication_id == publication_id:
                return KnowledgeSitePublicationState(
                    state=current.switch_origin_state or "active",
                    active_publication_id=current.active_publication_id,
                    pending_publication_id=publication_id,
                    error_code=current.error_code,
                )
            raise RuntimeError("another site publication switch is already in progress")
        if current.state not in {"active", "recovery_required"}:
            raise RuntimeError("site publication is not available for switching: " + current.state)
        self.publication_states[key] = KnowledgeSitePublicationState(
            state="switching",
            active_publication_id=current.active_publication_id,
            pending_publication_id=publication_id,
            switch_origin_state=current.state,
        )
        return current

    async def complete_site_publication_switch(
        self,
        *,
        tenant_id: str,
        site_id: str,
        publication_id: str,
    ) -> None:
        key = (tenant_id, site_id)
        current = await self.get_site_publication_state(tenant_id=tenant_id, site_id=site_id)
        if (
            current.state == "active"
            and current.active_publication_id == publication_id
            and current.pending_publication_id is None
        ):
            return
        if current.state != "switching" or current.pending_publication_id != publication_id:
            raise RuntimeError("site publication switch completion does not match pending state")
        self.publication_states[key] = KnowledgeSitePublicationState(
            state="active",
            active_publication_id=publication_id,
        )

    async def restore_site_publication(
        self,
        *,
        tenant_id: str,
        site_id: str,
        failed_publication_id: str,
        previous_publication_id: str | None,
        error_code: str,
    ) -> None:
        key = (tenant_id, site_id)
        current = await self.get_site_publication_state(tenant_id=tenant_id, site_id=site_id)
        if (
            current.state == "active"
            and current.active_publication_id == previous_publication_id
            and current.pending_publication_id is None
        ):
            return
        if current.state == "active":
            if (
                current.active_publication_id != failed_publication_id
                or current.pending_publication_id is not None
            ):
                raise RuntimeError("site publication rollback does not match active state")
        elif (
            current.state not in {"switching", "recovery_required"}
            or current.pending_publication_id != failed_publication_id
        ):
            raise RuntimeError("site publication rollback does not match pending state")
        self.publication_states[key] = KnowledgeSitePublicationState(
            state="active",
            active_publication_id=previous_publication_id,
            error_code=error_code,
        )

    async def require_site_publication_recovery(
        self,
        *,
        tenant_id: str,
        site_id: str,
        publication_id: str,
        error_code: str,
    ) -> None:
        key = (tenant_id, site_id)
        current = await self.get_site_publication_state(tenant_id=tenant_id, site_id=site_id)
        if (
            current.state == "recovery_required"
            and current.pending_publication_id == publication_id
            and current.error_code == error_code
        ):
            return
        if current.state == "active":
            active_publication_id = current.active_publication_id
        else:
            active_publication_id = current.active_publication_id
        if current.pending_publication_id not in {None, publication_id}:
            raise RuntimeError("site publication recovery does not match pending state")
        self.publication_states[key] = KnowledgeSitePublicationState(
            state="recovery_required",
            active_publication_id=active_publication_id,
            pending_publication_id=publication_id,
            error_code=error_code,
        )

    async def begin_sync(
        self,
        *,
        tenant_id: str,
        vault_path: str,
        actor_subject_id: str | None = None,
        correlation_id: str | None = None,
        approval_reference: str | None = None,
        sync_job_id: str | None = None,
    ) -> str:
        del tenant_id, vault_path, actor_subject_id, correlation_id, approval_reference
        return sync_job_id or str(uuid4())

    async def count_indexed_chunks(
        self,
        *,
        tenant_id: str,
        version_ids: Sequence[str],
    ) -> int:
        desired = set(version_ids)
        latest: dict[str, tuple[str, ...]] = {}
        for stored_tenant, _document_id, version_id, point_ids in self.indexed:
            if stored_tenant == tenant_id and version_id in desired:
                latest[version_id] = point_ids
        return sum(len(point_ids) for point_ids in latest.values())

    async def is_reusable_site_version(
        self,
        *,
        tenant_id: str,
        site_id: str,
        version_id: str,
        publication_id: str,
    ) -> bool:
        del site_id, publication_id
        snapshot = self.version_snapshots.get((tenant_id, version_id))
        return (
            snapshot is not None
            and snapshot.status != "discarded"
            and snapshot.index_status
            in {
                "indexed",
                "active",
                "staged",
            }
        )

    async def validate_site_publication_versions(
        self,
        *,
        tenant_id: str,
        site_id: str,
        publication_id: str,
        version_ids: Sequence[str],
    ) -> tuple[str, ...]:
        issues = []
        for version_id in dict.fromkeys(version_ids):
            if not await self.is_reusable_site_version(
                tenant_id=tenant_id,
                site_id=site_id,
                version_id=version_id,
                publication_id=publication_id,
            ):
                issues.append(f"version_id={version_id}:stale_or_unpublishable")
        return tuple(issues)

    async def invalid_site_publication_version_ids(
        self,
        *,
        tenant_id: str,
        site_id: str,
        publication_id: str,
        version_ids: Sequence[str],
    ) -> tuple[str, ...]:
        return tuple(
            version_id
            for version_id in dict.fromkeys(version_ids)
            if not await self.is_reusable_site_version(
                tenant_id=tenant_id,
                site_id=site_id,
                version_id=version_id,
                publication_id=publication_id,
            )
        )

    async def get_latest_version(
        self, *, tenant_id: str, document_id: str
    ) -> KnowledgeVersionSnapshot | None:
        return self.versions.get((tenant_id, document_id))

    async def stage_version(
        self,
        *,
        draft: KnowledgeVersionDraft,
        chunks: Sequence[KnowledgeChunkRecord],
    ) -> str:
        self.staged.append(draft)
        snapshot = KnowledgeVersionSnapshot(
            version_id=draft.version_id,
            content_hash=draft.content_hash,
            status=draft.status,
            index_status="staged",
            index_namespace=str(draft.metadata.get("index_namespace") or "") or None,
        )
        self.version_snapshots[(draft.tenant_id, draft.version_id)] = snapshot
        self.versions.setdefault((draft.tenant_id, draft.document_id), snapshot)
        return draft.version_id

    async def mark_indexed(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
        point_ids: Sequence[str],
        index_namespace: str,
        activate: bool = True,
    ) -> None:
        self.indexed.append((tenant_id, document_id, version_id, tuple(point_ids)))
        snapshot = self.version_snapshots[(tenant_id, version_id)]
        updated = replace(
            snapshot,
            index_status="active" if activate else "indexed",
            index_namespace=index_namespace,
        )
        self.version_snapshots[(tenant_id, version_id)] = updated
        current = self.versions.get((tenant_id, document_id))
        if current is None or current.version_id == version_id or activate:
            self.versions[(tenant_id, document_id)] = updated

    async def activate_document_version(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str | None,
    ) -> None:
        current = self.versions.get((tenant_id, document_id))
        if current is not None and current.version_id != version_id:
            self.version_snapshots[(tenant_id, current.version_id)] = replace(
                current, index_status="indexed"
            )
        if version_id is None:
            self.versions.pop((tenant_id, document_id), None)
            return
        snapshot = self.version_snapshots[(tenant_id, version_id)]
        updated = replace(snapshot, index_status="active")
        self.version_snapshots[(tenant_id, version_id)] = updated
        self.versions[(tenant_id, document_id)] = updated

    async def activate_site_versions(
        self,
        *,
        tenant_id: str,
        site_id: str,
        version_ids: Sequence[str],
        expected_point_count: int | None = None,
    ) -> None:
        del expected_point_count
        active = set(version_ids)
        self.activated_sites.append((tenant_id, site_id, tuple(version_ids)))
        drafts = {draft.version_id: draft for draft in self.staged}
        for key, snapshot in tuple(self.version_snapshots.items()):
            if key[0] != tenant_id:
                continue
            draft = drafts.get(snapshot.version_id)
            if draft is None or draft.metadata.get("site_id") != site_id:
                continue
            updated = replace(
                snapshot,
                index_status="active" if snapshot.version_id in active else "indexed",
            )
            self.version_snapshots[key] = updated
            if snapshot.version_id in active:
                self.versions[(tenant_id, draft.document_id)] = updated

    async def discard_site_snapshot_versions(
        self,
        *,
        tenant_id: str,
        site_id: str,
        snapshot_id: str,
    ) -> None:
        drafts = {draft.version_id: draft for draft in self.staged}
        for key, snapshot in tuple(self.version_snapshots.items()):
            if key[0] != tenant_id:
                continue
            draft = drafts.get(snapshot.version_id)
            if (
                draft is None
                or draft.metadata.get("site_id") != site_id
                or draft.metadata.get("snapshot_id") != snapshot_id
            ):
                continue
            discarded = replace(snapshot, status="discarded", index_status="discarded")
            self.version_snapshots[key] = discarded
            current = self.versions.get((tenant_id, draft.document_id))
            if current is not None and current.version_id == snapshot.version_id:
                self.versions.pop((tenant_id, draft.document_id), None)

    async def discard_staged_document(self, **values: Any) -> int:
        tenant_id = values["tenant_id"]
        site_id = values["site_id"]
        snapshot_id = values["snapshot_id"]
        document_id = values["document_id"]
        drafts = {draft.version_id: draft for draft in self.staged}
        version_ids = {
            snapshot.version_id
            for (stored_tenant, _version_id), snapshot in self.version_snapshots.items()
            if stored_tenant == tenant_id
            and snapshot.index_status in {"pending", "staged", "indexing"}
            and (
                (draft := drafts.get(snapshot.version_id)) is not None
                and draft.document_id == document_id
                and draft.metadata.get("site_id") == site_id
                and draft.metadata.get("snapshot_id") == snapshot_id
            )
        }
        for key, snapshot in tuple(self.version_snapshots.items()):
            if snapshot.version_id in version_ids:
                self.version_snapshots[key] = replace(
                    snapshot, status="discarded", index_status="discarded"
                )
        current = self.versions.get((tenant_id, document_id))
        if current is not None and current.version_id in version_ids:
            self.versions.pop((tenant_id, document_id), None)
        return len(version_ids)

    async def mark_excluded(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
    ) -> None:
        self.excluded.append((tenant_id, document_id, version_id))
        snapshot = self.versions[(tenant_id, document_id)]
        self.versions[(tenant_id, document_id)] = replace(snapshot, index_status="excluded")

    async def record_ingestion_rejection(
        self,
        *,
        tenant_id: str,
        sync_job_id: str,
        source_path: str,
        reason_code: str,
    ) -> None:
        self.ingestion_rejections.append((tenant_id, sync_job_id, source_path, reason_code))

    async def record_conflict(
        self,
        *,
        tenant_id: str,
        question: str,
        version_ids: Sequence[str],
        risk_level: int,
    ) -> str:
        self.conflicts.append((tenant_id, question, tuple(version_ids), risk_level))
        return f"conflict-{len(self.conflicts)}"

    async def complete_sync(self, **values: Any) -> None:
        self.completed_jobs.append(values)

    async def fail_sync(self, **values: Any) -> None:
        self.failed_jobs.append(values)


class InMemoryProductCatalog:
    def __init__(self, products: Sequence[ProductSnapshot] = ()) -> None:
        self.products: dict[tuple[str, str, str, str], ProductSnapshot] = {
            (item.tenant_id, item.site_id, item.snapshot_id, item.product_key): item
            for item in products
        }
        self.active_snapshots: dict[tuple[str, str], str] = {
            (item.tenant_id, item.site_id): item.snapshot_id for item in products
        }
        self.failed_snapshots: list[tuple[str, str, str, dict[str, str]]] = []

    async def begin_snapshot(self, **values: Any) -> None:
        del values

    async def stage_products(
        self,
        *,
        tenant_id: str,
        site_id: str,
        snapshot_id: str,
        products: Sequence[ProductSnapshot],
    ) -> None:
        for item in products:
            key = (tenant_id, site_id, snapshot_id, item.product_key)
            existing = self.products.get(key)
            if existing is not None:
                conflicts = product_identity_conflicts(existing, item)
                if conflicts:
                    raise ProductIdentityConflictError(
                        product_key=item.product_key,
                        fields=conflicts,
                        existing_url=existing.canonical_url,
                        candidate_url=item.canonical_url,
                    )
            self.products[key] = item

    async def discard_staged_product(self, **values: Any) -> None:
        tenant_id = values["tenant_id"]
        site_id = values["site_id"]
        snapshot_id = values["snapshot_id"]
        normalized_key = values["normalized_product_key"]
        canonical_url = values["canonical_url"]
        for key, item in tuple(self.products.items()):
            if (
                key[:3] == (tenant_id, site_id, snapshot_id)
                and item.normalized_product_key == normalized_key
                and item.canonical_url == canonical_url
            ):
                self.products.pop(key, None)

    async def count_staged_products(
        self,
        *,
        tenant_id: str,
        site_id: str,
        snapshot_id: str,
    ) -> int:
        return sum(
            (stored_tenant, stored_site, stored_snapshot) == (tenant_id, site_id, snapshot_id)
            for stored_tenant, stored_site, stored_snapshot, _key in self.products
        )

    async def activate_snapshot(
        self,
        *,
        tenant_id: str,
        site_id: str,
        snapshot_id: str,
        completed_at: datetime,
        missing_confirmation_threshold: int = 2,
    ) -> ProductSnapshotActivation:
        del completed_at, missing_confirmation_threshold
        self.active_snapshots[(tenant_id, site_id)] = snapshot_id
        active = [
            item
            for (stored_tenant, stored_site, stored_snapshot, _key), item in self.products.items()
            if (stored_tenant, stored_site, stored_snapshot) == (tenant_id, site_id, snapshot_id)
        ]
        return ProductSnapshotActivation(
            snapshot_id=snapshot_id,
            activated_count=sum(item.status is ProductDataStatus.VALID for item in active),
            pending_removal_count=sum(
                item.status is ProductDataStatus.PENDING_REMOVAL for item in active
            ),
            expired_count=sum(item.status is ProductDataStatus.EXPIRED for item in active),
        )

    async def fail_snapshot(
        self,
        *,
        tenant_id: str,
        site_id: str,
        snapshot_id: str,
        error_summary: dict[str, str],
    ) -> None:
        self.failed_snapshots.append((tenant_id, site_id, snapshot_id, dict(error_summary)))

    async def restore_snapshot(
        self,
        *,
        tenant_id: str,
        site_id: str,
        failed_snapshot_id: str,
        previous_snapshot_id: str | None,
        restored_at: datetime,
        error_summary: dict[str, str],
    ) -> None:
        del restored_at
        if previous_snapshot_id is None:
            self.active_snapshots.pop((tenant_id, site_id), None)
        else:
            self.active_snapshots[(tenant_id, site_id)] = previous_snapshot_id
        self.failed_snapshots.append((tenant_id, site_id, failed_snapshot_id, dict(error_summary)))

    async def find_exact(self, lookup: ProductLookup) -> ProductSnapshot | None:
        candidates = [
            item
            for (tenant_id, site_id, snapshot_id, _key), item in self.products.items()
            if tenant_id == lookup.tenant_id
            and (lookup.site_id is None or site_id == lookup.site_id)
            and self.active_snapshots.get((tenant_id, site_id)) == snapshot_id
        ]
        identifiers = {value.casefold() for value in (lookup.sku, lookup.mpn) if value is not None}
        for item in candidates:
            if lookup.canonical_url and item.canonical_url == lookup.canonical_url:
                return item
            if lookup.page_path and item.canonical_url.endswith(lookup.page_path):
                return item
            if identifiers & {
                value.casefold() for value in (item.sku, item.mpn) if value is not None
            }:
                return item
        return None

    async def list_active_products(
        self,
        *,
        tenant_id: str,
        site_id: str | None = None,
        limit: int = 200,
    ) -> tuple[ProductSnapshot, ...]:
        return tuple(
            item
            for (stored_tenant, stored_site, snapshot_id, _key), item in self.products.items()
            if stored_tenant == tenant_id
            and (site_id is None or stored_site == site_id)
            and self.active_snapshots.get((stored_tenant, stored_site)) == snapshot_id
            and item.status is ProductDataStatus.VALID
        )[:limit]

    async def list_active_products_by_keys(
        self,
        *,
        tenant_id: str,
        site_id: str | None,
        product_keys: Sequence[str],
    ) -> tuple[ProductSnapshot, ...]:
        products = await self.list_active_products(
            tenant_id=tenant_id,
            site_id=site_id,
            limit=max(1, len(self.products)),
        )
        by_key = {item.product_key: item for item in products}
        return tuple(by_key[key] for key in product_keys if key in by_key)

    async def get_active_summary(
        self,
        *,
        tenant_id: str,
        site_id: str,
    ) -> ActiveProductCatalogSummary:
        snapshot_id = self.active_snapshots.get((tenant_id, site_id))
        if snapshot_id is None:
            return ActiveProductCatalogSummary()
        count = sum(
            stored_tenant == tenant_id
            and stored_site == site_id
            and stored_snapshot == snapshot_id
            and item.status is ProductDataStatus.VALID
            for (stored_tenant, stored_site, stored_snapshot, _key), item in self.products.items()
        )
        completed = max(
            (
                item.fetched_at
                for (
                    stored_tenant,
                    stored_site,
                    stored_snapshot,
                    _key,
                ), item in self.products.items()
                if (stored_tenant, stored_site, stored_snapshot)
                == (tenant_id, site_id, snapshot_id)
            ),
            default=None,
        )
        return ActiveProductCatalogSummary(snapshot_id, count, completed)


class GroundedChatModel:
    async def generate(self, request: ChatModelRequest) -> ChatModelResult:
        language = str(
            (request.metadata.get("language_context") or {}).get("target_language") or "en"
        ).casefold()
        if request.metadata.get("task") == "general_guidance":
            return ChatModelResult(
                text=(
                    "我们建议用温水和温和清洁剂轻柔清洁 TPE，彻底阴干后再收纳。"
                    if language.startswith("zh")
                    else "We recommend gently cleaning TPE with warm water and a mild cleanser, "
                    "then allowing it to air-dry fully before storage."
                ),
                model="test",
                metadata={"general_guidance": True},
            )
        evidence = request.metadata.get("evidence", [])
        if not evidence:
            return ChatModelResult(text="", model="test", metadata={"grounded": False})
        return ChatModelResult(
            text=(
                f"根据当前商品信息：{evidence[0]['text']}"
                if language.startswith("zh")
                else f"Here is the relevant store information: {evidence[0]['text']}"
            ),
            model="test",
            metadata={"grounded": True},
        )


class DeterministicEmbeddingProvider:
    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[float(len(text)), 1.0] for text in texts]


class DeterministicSparseEmbeddingProvider:
    async def embed_sparse(self, texts: Sequence[str]) -> list[SparseEmbedding]:
        return [SparseEmbedding(indices=(len(text),), values=(1.0,)) for text in texts]


class AsyncCloser:
    async def check(self) -> None:
        return None

    async def initialize(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def dispose(self) -> None:
        return None


class InMemoryOrderRepository:
    def __init__(self, orders: Sequence[Order] = ()) -> None:
        self.orders = {(item.tenant_id, item.customer_id, item.order_id): item for item in orders}
        self.calls: list[tuple[str, str, str]] = []
        self.error: Exception | None = None

    async def get_for_customer(
        self,
        *,
        tenant_id: str,
        customer_id: str,
        order_id: str,
    ) -> Order | None:
        self.calls.append((tenant_id, customer_id, order_id))
        if self.error is not None:
            raise self.error
        return self.orders.get((tenant_id, customer_id, order_id))


class InMemorySupportTicketRepository:
    def __init__(self, tickets: Sequence[SupportTicket] = ()) -> None:
        self.tickets = {
            (item.tenant_id, item.customer_id, item.ticket_id): item for item in tickets
        }
        self.calls: list[tuple[str, str, str]] = []
        self.error: Exception | None = None

    async def get_for_customer(
        self,
        *,
        tenant_id: str,
        customer_id: str,
        ticket_id: str,
    ) -> SupportTicket | None:
        self.calls.append((tenant_id, customer_id, ticket_id))
        if self.error is not None:
            raise self.error
        return self.tickets.get((tenant_id, customer_id, ticket_id))


class FakeMockBusinessDataSeeder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.queue_calls: list[str] = []

    async def ensure_support_queues(self, *, tenant_id: str) -> None:
        self.queue_calls.append(tenant_id)

    async def seed(self, *, tenant_id: str, customer_id: str) -> None:
        self.calls.append((tenant_id, customer_id))
