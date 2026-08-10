import json
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import app.bootstrap.lifespan as lifespan_module
from app.api.routes.widget_assets import widget_asset_version
from app.application.dto import ListSitesResult, WebCrawlPreflightResult
from app.application.dto.widget_assets import PublicWidgetAssetResult
from app.application.services import (
    AnswerKnowledgeService,
    CheckReadinessService,
    CreateHandoffService,
    HandleChatService,
    ListHandoffsService,
    PublicWidgetSessionService,
    QueryBusinessDataService,
    SiteWebSourceConfigService,
    SkeletonResponseService,
    VisitorPresenceService,
    WebSyncJobService,
    default_widget_config,
)
from app.config import Settings
from app.domain.models import (
    AuthenticatedPrincipal,
    KnowledgeEvidence,
    Order,
    PublicWidgetSite,
    SiteWebDiscoveryMode,
    SiteWebSourceConfig,
    SiteWebSourceValidationStatus,
    SupportSite,
    WebCrawlManifest,
    WebCrawlManifestItem,
    WebCrawlManifestStatus,
    WebSyncJob,
    WebSyncJobStatus,
)
from app.domain.ports.web_sync_jobs import WebSyncSourceConfigVersionConflictError
from app.graphs import LangGraphAgentRunner, build_customer_support_graph
from app.integrations.auth import (
    DisabledAuthenticationAdapter,
    HmacPublicWidgetTokenAdapter,
    MockAuthenticationAdapter,
    StaticWidgetSiteAuthenticationAdapter,
)
from app.integrations.presence import InMemoryVisitorPresenceStore
from app.integrations.rate_limit import InMemoryWidgetRateLimitAdapter
from app.knowledge.models import KnowledgeSyncReport
from app.knowledge.web import WebCrawlPolicy, WebKnowledgeSyncReport
from app.main import create_app
from app.observability import InMemoryRequestMetrics
from app.realtime import InMemoryRealtimeHub
from tests.fakes.adapters import (
    AsyncCloser,
    FakeMockBusinessDataSeeder,
    GroundedChatModel,
    InMemoryConversationPersistenceAdapter,
    InMemoryHandoffAdapter,
    InMemoryKnowledgeControlPlane,
    InMemoryKnowledgeRetriever,
    InMemoryOrderRepository,
    InMemorySupportTicketRepository,
    InMemoryWebCrawlManifestStore,
)


class InMemorySiteWebSourceStore:
    def __init__(
        self,
        config: SiteWebSourceConfig | None = None,
        *,
        verified_base_urls: tuple[str, ...] = ("https://shop.example.com",),
    ) -> None:
        self.config = config
        self.verified_base_urls = verified_base_urls
        self.validation_updates: list[dict[str, object]] = []

    async def get_site_base_url(self, *, tenant_id: str, site_id: str) -> str | None:
        if (tenant_id, site_id) == ("tenant-a", "site-a"):
            return "https://shop.example.com"
        return None

    async def list_active_verified_site_base_urls(
        self,
        *,
        tenant_id: str,
    ) -> tuple[str, ...]:
        return self.verified_base_urls if tenant_id == "tenant-a" else ()

    async def get_config(self, *, tenant_id: str, site_id: str):  # type: ignore[no-untyped-def]
        del tenant_id, site_id
        return self.config

    async def put_config(self, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("the API test does not update source configuration")

    async def mark_validation_status(self, **_kwargs):  # type: ignore[no-untyped-def]
        self.validation_updates.append(_kwargs)
        return self.config


class FakeKnowledgeSyncService:
    def __init__(self, tenant_id: str = "tenant-a", sync_job_id: str = "sync-1") -> None:
        self._tenant_id = tenant_id
        self._sync_job_id = sync_job_id

    async def sync(self, tenant_id: str) -> KnowledgeSyncReport:
        assert tenant_id == self._tenant_id
        return KnowledgeSyncReport(
            sync_job_id=self._sync_job_id,
            discovered_count=1,
            indexed_count=1,
            skipped_count=0,
            excluded_count=0,
            failed_count=0,
            errors={},
        )


class FakeAdminSessionService:
    async def authenticate(self, command):  # type: ignore[no-untyped-def]
        if command.session_token != "valid-session":
            raise PermissionError("administrative session is invalid or expired")
        return SimpleNamespace(
            principal=AuthenticatedPrincipal(
                subject_id="admin-a",
                tenant_id="tenant-a",
                roles=frozenset({"admin"}),
                scopes=frozenset({"support:read"}),
                authentication_method="session",
                authenticated_at=datetime.now(UTC),
                correlation_id=command.correlation_id,
            )
        )


class FakeWebKnowledgeSyncService:
    last_policy_seen: WebCrawlPolicy | None = None

    def __init__(self) -> None:
        self.last_policy: WebCrawlPolicy | None = None

    async def sync(self, policy: WebCrawlPolicy) -> WebKnowledgeSyncReport:
        self.last_policy = policy
        type(self).last_policy_seen = policy
        assert policy.tenant_id == "tenant-a"
        assert policy.site_id == "site-a"
        assert policy.base_url == "https://shop.example.com"
        return WebKnowledgeSyncReport(
            sync_job_id="web-sync-1",
            discovered_count=2,
            document_count=1,
            indexed_count=3,
            skipped_count=0,
            excluded_count=1,
            failed_count=0,
            errors={},
        )


class FakeSupportOperationsService:
    def __init__(self, *, verification_status: str = "verified") -> None:
        self._verification_status = verification_status

    async def list_sites(self, _query):  # type: ignore[no-untyped-def]
        now = datetime.now(UTC)
        return ListSitesResult(
            items=(
                SupportSite(
                    site_id="site-a",
                    tenant_id="tenant-a",
                    name="Example Shop",
                    base_url="https://shop.example.com",
                    status="active",
                    created_at=now,
                    updated_at=now,
                    verification_status=self._verification_status,
                ),
            )
        )


class FakeWebCrawlPreflightService:
    last_command = None

    def __init__(
        self,
        store: InMemoryWebCrawlManifestStore,
        manifest: WebCrawlManifest,
    ) -> None:
        self._store = store
        self._manifest = manifest

    async def run(self, command) -> WebCrawlPreflightResult:  # type: ignore[no-untyped-def]
        type(self).last_command = command
        manifest = replace(
            self._manifest,
            discovery_method="manual" if command.explicit_sitemap_urls else "common_path",
            root_sitemap_urls=command.explicit_sitemap_urls,
            source_config_version=command.source_config_version,
        )
        return WebCrawlPreflightResult(await self._store.save(manifest))

    async def get_latest(self, query):  # type: ignore[no-untyped-def]
        return await self._store.get_latest(
            tenant_id=query.principal.tenant_id,
            site_id=query.site_id,
        )


def _web_manifest() -> WebCrawlManifest:
    return WebCrawlManifest(
        tenant_id="tenant-a",
        site_id="site-a",
        manifest_id="manifest-12345678",
        base_url="https://shop.example.com",
        root_sitemap_url="https://shop.example.com/sitemap.xml",
        primary_language="en",
        translation_provider="gtranslate",
        status=WebCrawlManifestStatus.READY,
        fingerprint="a" * 64,
        primary_sitemap_urls=("https://shop.example.com/sitemap-products.xml",),
        translated_locales=("de", "fr"),
        excluded_sitemap_count=2,
        excluded_url_count=0,
        blocking_reasons=(),
        created_by="customer-1",
        created_at=datetime.now(UTC),
        items=tuple(
            WebCrawlManifestItem(
                url=f"https://shop.example.com/product-{index}.html",
                source_sitemap_url="https://shop.example.com/sitemap-products.xml",
            )
            for index in range(500)
        ),
    )


class InMemoryWebSyncJobStore:
    def __init__(self, *, source_config_version: int = 0) -> None:
        self.jobs: dict[str, WebSyncJob] = {}
        self.items: dict[str, tuple] = {}
        self.source_config_version = source_config_version

    async def enqueue(  # type: ignore[no-untyped-def]
        self,
        job: WebSyncJob,
        items=(),
        *,
        expected_source_config_version: int = 0,
    ) -> tuple[WebSyncJob, bool]:
        if expected_source_config_version != self.source_config_version:
            raise WebSyncSourceConfigVersionConflictError
        active = next(
            (
                item
                for item in self.jobs.values()
                if item.site_id == job.site_id
                and item.status.value in {"preparing", "queued", "running"}
            ),
            None,
        )
        if active is not None:
            return active, False
        self.jobs[job.job_id] = job
        self.items[job.job_id] = tuple(items)
        return job, True

    async def get(self, *, tenant_id: str, job_id: str) -> WebSyncJob | None:
        job = self.jobs.get(job_id)
        return job if job is not None and job.tenant_id == tenant_id else None

    async def list_recent(
        self, *, tenant_id: str, site_id: str | None, limit: int
    ) -> tuple[WebSyncJob, ...]:
        jobs = [
            job
            for job in self.jobs.values()
            if job.tenant_id == tenant_id and (site_id is None or job.site_id == site_id)
        ]
        return tuple(sorted(jobs, key=lambda item: item.requested_at, reverse=True)[:limit])

    async def request_cancel(self, **kwargs) -> WebSyncJob:  # type: ignore[no-untyped-def]
        job = self.jobs[kwargs["job_id"]]
        canceled = replace(
            job,
            status=WebSyncJobStatus.CANCELED,
            cancel_requested_at=kwargs["requested_at"],
            completed_at=kwargs["requested_at"],
        )
        self.jobs[job.job_id] = canceled
        return canceled

    async def list_items(self, **kwargs) -> tuple:  # type: ignore[no-untyped-def]
        return self.items.get(kwargs["job_id"], ())[: kwargs["limit"]]


class FakePublicWidgetAccess:
    def __init__(self) -> None:
        widget_config = replace(
            default_widget_config("en"),
            agent_avatar_url="https://untrusted.example/avatar.png",
            launcher_asset_id="11111111-1111-1111-1111-111111111111",
            launcher_image_fit="cover",
            agent_avatar_asset_id="22222222-2222-2222-2222-222222222222",
        )
        self.site = PublicWidgetSite(
            public_widget_id="site_pub_abcdefghijklmnop",
            tenant_id="tenant-a",
            site_id="site-a",
            allowed_origins=("https://shop.example.com",),
            status="active",
            daily_message_limit=100,
            widget_config=widget_config,
            widget_config_version="widget-version-7",
        )
        self.admissions: set[str] = set()

    async def get_public_site(self, *, public_widget_id: str):  # type: ignore[no-untyped-def]
        return self.site if public_widget_id == self.site.public_widget_id else None

    async def admit_message(self, *, request_id: str, **_kwargs) -> bool:  # type: ignore[no-untyped-def]
        self.admissions.add(request_id)
        return True


class FakeWidgetAssetService:
    async def read_public(self, query):  # type: ignore[no-untyped-def]
        if query.asset_id != "11111111-1111-1111-1111-111111111111" or query.size != 128:
            raise LookupError("widget image was not found")
        return PublicWidgetAssetResult(b"managed-webp")


class FakeConnectorSiteAdministrationService:
    async def connector_manifest(self, principal):  # type: ignore[no-untyped-def]
        return SimpleNamespace(
            public_widget_id="site_pub_abcdefghijklmnop",
            site_id=principal.site_id,
        )


def client(
    monkeypatch,
    *,
    web_crawler_enabled: bool = False,
    web_sync_worker_heartbeat_path: str = "",
    site_verification_status: str = "verified",
    web_source_store: InMemorySiteWebSourceStore | None = None,
) -> TestClient:  # type: ignore[no-untyped-def]
    async def fake_build_container(settings: Settings):  # type: ignore[no-untyped-def]
        settings.web_crawler_enabled = web_crawler_enabled
        settings.public_widget_base_url = "https://widget.example.com"
        settings.web_sync_worker_heartbeat_path = web_sync_worker_heartbeat_path
        settings.embedding_provider = "fake"
        handoffs = InMemoryHandoffAdapter()
        conversations = InMemoryConversationPersistenceAdapter()
        knowledge_service = AnswerKnowledgeService(
            retriever=InMemoryKnowledgeRetriever(
                [
                    KnowledgeEvidence(
                        chunk_id="chunk-1",
                        document_id="document-1",
                        text="产品支持通过设置页面启用。",
                        score=0.9,
                        source="guide.md",
                        metadata={"tenant_id": "tenant-a", "version_id": "version-1"},
                    )
                ]
            ),
            chat_model=GroundedChatModel(),
            control_plane=InMemoryKnowledgeControlPlane(),
        )
        business_service = QueryBusinessDataService(
            orders=InMemoryOrderRepository(
                [
                    Order(
                        order_id="DEMO-ORDER-1001",
                        tenant_id="tenant-a",
                        customer_id="customer-1",
                        status="shipped",
                        version=1,
                        updated_at=datetime.now(UTC),
                    )
                ]
            ),
            support_tickets=InMemorySupportTicketRepository(),
        )
        runner = LangGraphAgentRunner(
            build_customer_support_graph(
                response_service=SkeletonResponseService(),
                handoff_service=CreateHandoffService(handoffs),
                knowledge_answer_service=knowledge_service,
                business_query_service=business_service,
            )
        )
        widget_secret = "test-public-widget-token-secret-123456"
        effective_web_source_store = web_source_store or InMemorySiteWebSourceStore()
        web_sync_job_store = InMemoryWebSyncJobStore(
            source_config_version=(
                effective_web_source_store.config.config_version
                if effective_web_source_store.config is not None
                else 0
            )
        )
        web_manifest = _web_manifest()
        web_manifest_store = InMemoryWebCrawlManifestStore((web_manifest,))
        return SimpleNamespace(
            settings=settings,
            admin_session_service=None,
            realtime_hub=InMemoryRealtimeHub(),
            authentication=MockAuthenticationAdapter(subject_id="customer-1", tenant_id="tenant-a"),
            widget_authentication=StaticWidgetSiteAuthenticationAdapter(
                {
                    "test-widget-site-key": {
                        "tenant_id": "tenant-a",
                        "site_id": "site-a",
                    },
                    "test-widget-site-key-at-least-32-characters": {
                        "tenant_id": "tenant-a",
                        "site_id": "site-a",
                        "site_identity_version": "widget-version-7",
                    },
                }
            ),
            site_administration_service=FakeConnectorSiteAdministrationService(),
            public_widget_session_service=PublicWidgetSessionService(
                access=FakePublicWidgetAccess(),
                tokens=HmacPublicWidgetTokenAdapter(secret=widget_secret, ttl_seconds=900),
                rate_limits=InMemoryWidgetRateLimitAdapter(fingerprint_secret=widget_secret),
                bootstrap_limit_per_minute=30,
                chat_limit_per_minute=20,
                presence_limit_per_minute=12,
            ),
            widget_asset_service=FakeWidgetAssetService(),
            visitor_presence_service=VisitorPresenceService(
                InMemoryVisitorPresenceStore(),
                conversation_context=conversations,
            ),
            chat_service=HandleChatService(
                runner,
                conversations,
                conversation_context=conversations,
            ),
            list_handoffs_service=ListHandoffsService(handoffs),
            readiness_service=CheckReadinessService(
                {"postgres": AsyncCloser(), "qdrant": AsyncCloser()}
            ),
            knowledge_sync_service=FakeKnowledgeSyncService(),
            web_knowledge_sync_service=FakeWebKnowledgeSyncService(),
            web_sync_job_service=WebSyncJobService(web_sync_job_store, web_manifest_store),
            web_crawl_preflight_service=FakeWebCrawlPreflightService(
                web_manifest_store,
                web_manifest,
            ),
            site_web_source_config_service=SiteWebSourceConfigService(effective_web_source_store),
            support_operations_service=FakeSupportOperationsService(
                verification_status=site_verification_status
            ),
            global_knowledge_sync_service=FakeKnowledgeSyncService("__global__", "sync-global-1"),
            database=AsyncCloser(),
            knowledge_adapter=AsyncCloser(),
            mock_business_seeder=FakeMockBusinessDataSeeder(),
            request_metrics=InMemoryRequestMetrics(),
        )

    monkeypatch.setattr(lifespan_module, "build_container", fake_build_container)
    return TestClient(create_app())


def test_liveness(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    with client(monkeypatch) as test_client:
        response = test_client.get("/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_public_widget_appearance_is_origin_bound_and_cacheable(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    with client(monkeypatch) as test_client:
        response = test_client.get(
            "/v1/public-widget/appearance",
            params={"public_widget_id": "site_pub_abcdefghijklmnop"},
            headers={"Origin": "https://shop.example.com"},
        )
        denied = test_client.get(
            "/v1/public-widget/appearance",
            params={"public_widget_id": "site_pub_abcdefghijklmnop"},
            headers={"Origin": "https://evil.example"},
        )
        not_modified = test_client.get(
            "/v1/public-widget/appearance",
            params={"public_widget_id": "site_pub_abcdefghijklmnop"},
            headers={
                "Origin": "https://shop.example.com",
                "If-None-Match": response.headers["etag"],
            },
        )

    assert response.status_code == 200
    appearance = response.json()
    assert appearance["schema_version"] == 3
    assert appearance["version"] == appearance["config_version"] == "widget-version-7"
    assert appearance["asset_version"]
    assert appearance["agent_name"] == "在线客服"
    assert appearance["welcome_message"] == "Hello! How can I help you today?"
    assert appearance["launcher_image_url"] == (
        "https://widget.example.com/v1/widget-media/11111111-1111-1111-1111-111111111111?size=128"
    )
    assert appearance["agent_avatar_url"] == (
        "https://widget.example.com/v1/widget-media/22222222-2222-2222-2222-222222222222?size=64"
    )
    assert appearance["launcher_image_fit"] == "cover"
    assert appearance["primary_color"] == "#2563eb"
    assert appearance["position"] == "right"
    assert isinstance(appearance["is_online"], bool)
    assert response.headers["access-control-allow-origin"] == "https://shop.example.com"
    assert response.headers["cache-control"] == (
        "public, max-age=0, s-maxage=60, stale-while-revalidate=300"
    )
    assert response.headers["access-control-expose-headers"] == "ETag"
    assert denied.status_code == 403
    assert not_modified.status_code == 304


def test_public_widget_bootstrap_only_exposes_managed_image_urls(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    with client(monkeypatch) as test_client:
        response = test_client.post(
            "/v1/public-widget/bootstrap",
            content=json.dumps({"public_widget_id": "site_pub_abcdefghijklmnop"}),
            headers={
                "Origin": "https://shop.example.com",
                "Content-Type": "text/plain;charset=UTF-8",
            },
        )

    assert response.status_code == 200
    config = response.json()["widget_config"]
    assert config["launcher_image_url"].startswith("https://widget.example.com/v1/widget-media/")
    assert config["agent_avatar_url"].startswith("https://widget.example.com/v1/widget-media/")
    assert "untrusted.example" not in response.text
    assert "launcher_asset_id" not in config
    assert "agent_avatar_asset_id" not in config


def test_widget_connector_manifest_resolves_public_identity_server_side(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    with client(monkeypatch) as test_client:
        response = test_client.get(
            "/v1/widget/manifest",
            headers={
                "X-Agent-Site-Key": "test-widget-site-key-at-least-32-characters",
            },
        )

    assert response.status_code == 200
    manifest = response.json()
    assert manifest["public_widget_id"] == "site_pub_abcdefghijklmnop"
    assert manifest["script_url"].startswith("https://widget.example.com/widget.js?v=")
    assert manifest["asset_version"]
    assert manifest["config_version"] == "widget-version-7"
    assert manifest["connector_mode"] == "public"
    assert response.headers["cache-control"] == "no-store"


def test_public_widget_media_is_immutable_and_has_a_safe_missing_fallback(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    with client(monkeypatch) as test_client:
        response = test_client.get("/v1/widget-media/11111111-1111-1111-1111-111111111111?size=128")
        missing = test_client.get("/v1/widget-media/22222222-2222-2222-2222-222222222222?size=128")

    assert response.status_code == 200
    assert response.content == b"managed-webp"
    assert response.headers["content-type"] == "image/webp"
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert missing.status_code == 404


def test_support_websocket_rejects_missing_admin_session(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    with client(monkeypatch) as test_client:
        container = test_client.app.state.container
        container.admin_session_service = FakeAdminSessionService()
        container.settings.enforce_browser_origin = True
        container.settings.allowed_origins = ["http://testserver"]

        with pytest.raises(WebSocketDisconnect) as raised:
            with test_client.websocket_connect(
                "/v1/ws/support",
                headers={"origin": "http://testserver"},
            ):
                pass

    assert raised.value.code == 1008


def test_support_websocket_accepts_authenticated_admin_session(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    with client(monkeypatch) as test_client:
        container = test_client.app.state.container
        container.admin_session_service = FakeAdminSessionService()
        container.settings.enforce_browser_origin = True
        container.settings.allowed_origins = ["http://testserver"]
        test_client.cookies.set(container.settings.admin_session_cookie_name, "valid-session")

        with test_client.websocket_connect(
            "/v1/ws/support",
            headers={"origin": "http://testserver"},
        ) as websocket:
            connected = websocket.receive_json()
            websocket.send_text("ping")
            pong = websocket.receive_text()

    assert connected["event_type"] == "realtime.connected"
    assert connected["tenant_id"] == "tenant-a"
    assert connected["payload"]["subject_id"] == "admin-a"
    assert pong == "pong"


def test_request_metrics_middleware_records_api_requests(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    with client(monkeypatch) as test_client:
        response = test_client.get("/health/live")
        metrics = test_client.app.state.container.request_metrics.snapshot()

    assert response.status_code == 200
    assert metrics["request_count"] >= 1
    assert metrics["responses_2xx"] >= 1


def test_chat_returns_grounded_graph_response(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    with client(monkeypatch) as test_client:
        response = test_client.post("/v1/chat", json={"message": "如何使用产品？"})
    assert response.status_code == 200
    assert response.json()["risk_level"] == 0
    assert response.json()["handoff_id"] is None
    assert response.json()["citations"] == ["guide.md#chunk-1"]
    assert response.json()["related_links"] == []


def test_chat_routes_owned_order_status_to_human(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    with client(monkeypatch) as test_client:
        response = test_client.post(
            "/v1/chat",
            json={"message": "查询订单状态 DEMO-ORDER-1001"},
        )
    assert response.status_code == 200
    assert response.json()["kind"] == "handoff"
    assert "人工订单客服" in response.json()["message"]
    assert response.json()["citations"] == []
    assert response.json()["related_links"] == []


def test_chat_returns_handoff_id(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    with client(monkeypatch) as test_client:
        response = test_client.post("/v1/chat", json={"message": "我要退款"})
    assert response.status_code == 200
    assert response.json()["kind"] == "handoff"
    assert response.json()["handoff_id"]


def test_chat_rejects_request_body_identity(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    with client(monkeypatch) as test_client:
        response = test_client.post(
            "/v1/chat",
            json={"message": "如何使用产品？", "user_id": "attacker-controlled"},
        )
    assert response.status_code == 422


def test_knowledge_sync_uses_trusted_tenant(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    with client(monkeypatch) as test_client:
        response = test_client.post("/v1/knowledge/sync")
    assert response.status_code == 200
    assert response.json()["sync_job_id"] == "sync-1"
    assert response.json()["indexed_count"] == 1


def test_web_knowledge_sync_is_disabled_by_default(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    with client(monkeypatch) as test_client:
        response = test_client.post("/v1/knowledge/sync/web/site-a")

    assert response.status_code == 503
    assert response.json()["detail"] == "website knowledge synchronization is disabled"


def test_web_sync_availability_reports_operator_and_site_gates(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    with client(
        monkeypatch,
        site_verification_status="pending",
    ) as test_client:
        response = test_client.get("/v1/knowledge/web-sync-availability/site-a")

    assert response.status_code == 200
    assert response.json()["crawler_enabled"] is False
    assert response.json()["worker_status"] == "not_required"
    assert response.json()["preflight_ready"] is False
    assert response.json()["job_processing_ready"] is False
    assert response.json()["blocking_reasons"] == [
        "crawler_disabled",
        "site_not_verified",
    ]


def test_web_sync_availability_reports_missing_worker_heartbeat(
    monkeypatch,
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    heartbeat = tmp_path / "missing-worker.heartbeat"
    with client(
        monkeypatch,
        web_crawler_enabled=True,
        web_sync_worker_heartbeat_path=str(heartbeat),
    ) as test_client:
        response = test_client.get("/v1/knowledge/web-sync-availability/site-a")

    assert response.status_code == 200
    assert response.json()["worker_status"] == "unavailable"
    assert response.json()["preflight_ready"] is True
    assert response.json()["job_processing_ready"] is False
    assert response.json()["blocking_reasons"] == ["web_sync_worker_unavailable"]


def test_web_sync_availability_rejects_unconfigured_worker_health(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    with client(monkeypatch, web_crawler_enabled=True) as test_client:
        response = test_client.get("/v1/knowledge/web-sync-availability/site-a")

    assert response.status_code == 200
    assert response.json()["worker_status"] == "unknown"
    assert response.json()["preflight_ready"] is True
    assert response.json()["job_processing_ready"] is False
    assert response.json()["blocking_reasons"] == ["web_sync_worker_unavailable"]


def test_web_sync_job_rejects_unhealthy_worker(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    with client(monkeypatch, web_crawler_enabled=True) as test_client:
        response = test_client.post(
            "/v1/knowledge/web-sync-jobs/site-a",
            json={
                "idempotency_key": "worker-unavailable-request",
                "manifest_id": "manifest-12345678",
                "mode": "shadow",
                "sample_size": 20,
            },
        )

    assert response.status_code == 503
    assert response.json()["detail"] == ("website knowledge synchronization worker is not healthy")


def test_web_sync_availability_accepts_fresh_worker_heartbeat(
    monkeypatch,
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    heartbeat = tmp_path / "worker.heartbeat"
    heartbeat.touch()
    with client(
        monkeypatch,
        web_crawler_enabled=True,
        web_sync_worker_heartbeat_path=str(heartbeat),
    ) as test_client:
        response = test_client.get("/v1/knowledge/web-sync-availability/site-a")

    assert response.status_code == 200
    assert response.json()["worker_status"] == "healthy"
    assert response.json()["preflight_ready"] is True
    assert response.json()["job_processing_ready"] is True
    assert response.json()["blocking_reasons"] == []


def test_web_knowledge_sync_uses_trusted_site_configuration(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    FakeWebKnowledgeSyncService.last_policy_seen = None
    with client(monkeypatch, web_crawler_enabled=True) as test_client:
        response = test_client.post("/v1/knowledge/sync/web/site-a")

    assert response.status_code == 409
    assert "preflight" in response.json()["detail"]
    assert FakeWebKnowledgeSyncService.last_policy_seen is None


def test_web_knowledge_sync_accepts_tenant_site_seed_urls(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    FakeWebKnowledgeSyncService.last_policy_seen = None
    with client(monkeypatch, web_crawler_enabled=True) as test_client:
        response = test_client.post(
            "/v1/knowledge/sync/web/site-a",
            json={"urls": ["https://shop.example.com/products/model.html"]},
        )

    assert response.status_code == 409
    assert FakeWebKnowledgeSyncService.last_policy_seen is None


def test_web_knowledge_sync_rejects_unknown_tenant_site(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    with client(monkeypatch, web_crawler_enabled=True) as test_client:
        response = test_client.post("/v1/knowledge/sync/web/site-from-another-tenant")

    assert response.status_code == 404


def test_web_sync_job_is_queued_and_listed(
    monkeypatch,
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    heartbeat = tmp_path / "worker.heartbeat"
    heartbeat.touch()
    with client(
        monkeypatch,
        web_crawler_enabled=True,
        web_sync_worker_heartbeat_path=str(heartbeat),
    ) as test_client:
        queued = test_client.post(
            "/v1/knowledge/web-sync-jobs/site-a",
            json={
                "idempotency_key": "request-12345678",
                "manifest_id": "manifest-12345678",
                "mode": "shadow",
                "sample_size": 20,
            },
        )
        listed = test_client.get("/v1/knowledge/web-sync-jobs?site_id=site-a")

    assert queued.status_code == 202
    assert queued.json()["created"] is True
    assert queued.json()["job"]["status"] == "preparing"
    assert queued.json()["job"]["prepared_count"] == 0
    assert queued.json()["job"]["site_id"] == "site-a"
    assert queued.json()["job"]["mode"] == "shadow"
    assert queued.json()["job"]["publication_status"] == "not_requested"
    assert listed.status_code == 200
    assert listed.json()["items"][0]["job_id"] == queued.json()["job"]["job_id"]


def test_web_sync_job_deduplicates_active_site(
    monkeypatch,
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    heartbeat = tmp_path / "worker.heartbeat"
    heartbeat.touch()
    with client(
        monkeypatch,
        web_crawler_enabled=True,
        web_sync_worker_heartbeat_path=str(heartbeat),
    ) as test_client:
        first = test_client.post(
            "/v1/knowledge/web-sync-jobs/site-a",
            json={
                "idempotency_key": "request-first-123",
                "manifest_id": "manifest-12345678",
                "mode": "shadow",
                "sample_size": 20,
            },
        )
        second = test_client.post(
            "/v1/knowledge/web-sync-jobs/site-a",
            json={
                "idempotency_key": "request-second-456",
                "manifest_id": "manifest-12345678",
                "mode": "shadow",
                "sample_size": 20,
            },
        )

    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json()["created"] is False
    assert second.json()["job"]["job_id"] == first.json()["job"]["job_id"]


def test_web_sync_job_can_be_canceled_and_items_are_tenant_scoped(
    monkeypatch,
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    heartbeat = tmp_path / "worker.heartbeat"
    heartbeat.touch()
    with client(
        monkeypatch,
        web_crawler_enabled=True,
        web_sync_worker_heartbeat_path=str(heartbeat),
    ) as test_client:
        queued = test_client.post(
            "/v1/knowledge/web-sync-jobs/site-a",
            json={
                "idempotency_key": "request-cancel-123",
                "manifest_id": "manifest-12345678",
                "mode": "shadow",
                "sample_size": 20,
            },
        )
        job_id = queued.json()["job"]["job_id"]
        items = test_client.get(f"/v1/knowledge/web-sync-jobs/{job_id}/items")
        canceled = test_client.post(f"/v1/knowledge/web-sync-jobs/{job_id}/cancel")

    assert items.status_code == 200
    assert len(items.json()["items"]) == 0
    assert canceled.status_code == 200
    assert canceled.json()["status"] == "canceled"
    assert canceled.json()["cancel_requested_at"] is not None


def test_web_crawl_preflight_returns_primary_language_manifest(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    with client(monkeypatch, web_crawler_enabled=True) as test_client:
        response = test_client.post(
            "/v1/knowledge/web-crawl-preflights/site-a",
            json={"translation_provider": "gtranslate"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["url_count"] == 500
    assert response.json()["translated_locales"] == ["de", "fr"]
    assert response.json()["production_sync_enabled"] is False
    assert response.json()["source_config_version"] == 0
    assert response.json()["source_config_current"] is True


def test_changed_sitemap_source_invalidates_latest_manifest_and_enqueue(
    monkeypatch,
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    source_store = InMemorySiteWebSourceStore(
        SiteWebSourceConfig(
            tenant_id="tenant-a",
            site_id="site-a",
            discovery_mode=SiteWebDiscoveryMode.HYBRID,
            explicit_sitemap_urls=("https://shop.example.com/new-map.xml",),
            config_version=1,
            validation_status=SiteWebSourceValidationStatus.UNVALIDATED,
            validated_at=None,
            updated_by="admin-a",
            updated_at=datetime.now(UTC),
        )
    )
    heartbeat = tmp_path / "worker.heartbeat"
    heartbeat.touch()

    with client(
        monkeypatch,
        web_crawler_enabled=True,
        web_sync_worker_heartbeat_path=str(heartbeat),
        web_source_store=source_store,
    ) as test_client:
        latest = test_client.get("/v1/knowledge/web-crawl-preflights/site-a/latest")
        queued = test_client.post(
            "/v1/knowledge/web-sync-jobs/site-a",
            json={
                "idempotency_key": "stale-manifest-request",
                "manifest_id": "manifest-12345678",
                "mode": "shadow",
                "sample_size": 20,
            },
        )

    assert latest.status_code == 200
    assert latest.json()["source_config_version"] == 0
    assert latest.json()["source_config_current"] is False
    assert queued.status_code == 409
    assert "configuration changed" in queued.json()["detail"]


def test_web_crawl_preflight_uses_and_validates_site_sitemap_config(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    sitemap_url = "https://maps.example-cdn.com/custom-map?format=xml"
    source_store = InMemorySiteWebSourceStore(
        SiteWebSourceConfig(
            tenant_id="tenant-a",
            site_id="site-a",
            discovery_mode=SiteWebDiscoveryMode.MANUAL,
            explicit_sitemap_urls=(sitemap_url,),
            config_version=1,
            validation_status=SiteWebSourceValidationStatus.UNVALIDATED,
            validated_at=None,
            updated_by="admin-a",
            updated_at=datetime.now(UTC),
        ),
        verified_base_urls=(
            "https://shop.example.com",
            "https://maps.example-cdn.com/content",
        ),
    )

    with client(
        monkeypatch,
        web_crawler_enabled=True,
        web_source_store=source_store,
    ) as test_client:
        response = test_client.post(
            "/v1/knowledge/web-crawl-preflights/site-a",
            json={"translation_provider": "gtranslate"},
        )

    assert response.status_code == 200
    assert FakeWebCrawlPreflightService.last_command.discovery_mode == "manual"
    assert FakeWebCrawlPreflightService.last_command.explicit_sitemap_urls == (sitemap_url,)
    assert FakeWebCrawlPreflightService.last_command.allowed_sitemap_origins == (
        "https://maps.example-cdn.com",
        "https://shop.example.com",
    )
    assert source_store.validation_updates[0]["validation_status"] is (
        SiteWebSourceValidationStatus.VALID
    )


def test_web_crawl_preflight_does_not_validate_dormant_urls_in_auto_mode(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    source_store = InMemorySiteWebSourceStore(
        SiteWebSourceConfig(
            tenant_id="tenant-a",
            site_id="site-a",
            discovery_mode=SiteWebDiscoveryMode.AUTO,
            explicit_sitemap_urls=("https://shop.example.com/dormant-map.xml",),
            config_version=2,
            validation_status=SiteWebSourceValidationStatus.UNVALIDATED,
            validated_at=None,
            updated_by="admin-a",
            updated_at=datetime.now(UTC),
        )
    )

    with client(
        monkeypatch,
        web_crawler_enabled=True,
        web_source_store=source_store,
    ) as test_client:
        response = test_client.post(
            "/v1/knowledge/web-crawl-preflights/site-a",
            json={"translation_provider": "gtranslate"},
        )

    assert response.status_code == 200
    assert FakeWebCrawlPreflightService.last_command.discovery_mode == "auto"
    assert source_store.validation_updates == []


def test_web_crawl_preflight_rejects_a_concurrent_source_config_change(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    first = SiteWebSourceConfig(
        tenant_id="tenant-a",
        site_id="site-a",
        discovery_mode=SiteWebDiscoveryMode.AUTO,
        explicit_sitemap_urls=(),
        config_version=1,
        validation_status=SiteWebSourceValidationStatus.UNVALIDATED,
        validated_at=None,
        updated_by="admin-a",
        updated_at=datetime.now(UTC),
    )

    class ChangingSourceStore(InMemorySiteWebSourceStore):
        def __init__(self) -> None:
            super().__init__(first)
            self.read_count = 0

        async def get_config(self, *, tenant_id: str, site_id: str):  # type: ignore[no-untyped-def]
            del tenant_id, site_id
            self.read_count += 1
            return first if self.read_count == 1 else replace(first, config_version=2)

    with client(
        monkeypatch,
        web_crawler_enabled=True,
        web_source_store=ChangingSourceStore(),
    ) as test_client:
        response = test_client.post(
            "/v1/knowledge/web-crawl-preflights/site-a",
            json={"translation_provider": "gtranslate"},
        )

    assert response.status_code == 409
    assert "changed during preflight" in response.json()["detail"]


def test_global_knowledge_sync_uses_reserved_partition(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    with client(monkeypatch) as test_client:
        response = test_client.post("/v1/knowledge/sync/global")
    assert response.status_code == 200
    assert response.json()["sync_job_id"] == "sync-global-1"
    assert response.json()["indexed_count"] == 1


def test_handoff_queue_returns_tenant_scoped_created_handoff(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    with client(monkeypatch) as test_client:
        created = test_client.post("/v1/chat", json={"message": "我要退款"})
        queue = test_client.get("/v1/handoffs")

    assert created.status_code == 200
    assert queue.status_code == 200
    assert len(queue.json()["items"]) == 1
    assert queue.json()["items"][0]["handoff_id"] == created.json()["handoff_id"]
    assert queue.json()["items"][0]["status"] == "pending"


def test_widget_chat_uses_server_site_credential(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    with client(monkeypatch) as test_client:
        response = test_client.post(
            "/v1/widget/chat",
            headers={"X-Agent-Site-Key": "test-widget-site-key"},
            json={"message": "如何使用产品？"},
        )

    assert response.status_code == 200
    assert response.json()["kind"] == "answer"
    assert response.json()["citations"] == ["guide.md#chunk-1"]
    assert response.json()["related_links"] == []


def test_widget_messages_uses_server_site_credential(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    with client(monkeypatch) as test_client:
        response = test_client.post(
            "/v1/widget/messages",
            headers={"X-Agent-Site-Key": "test-widget-site-key"},
            json={"conversation_id": "conversation-1"},
        )

    assert response.status_code == 200
    assert response.json()["items"] == []


def test_public_widget_bootstrap_and_chat_need_only_install_id(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    origin = "https://shop.example.com"
    headers = {"Origin": origin, "Content-Type": "text/plain;charset=UTF-8"}
    with client(monkeypatch) as test_client:
        bootstrap_response = test_client.post(
            "/v1/public-widget/bootstrap",
            headers=headers,
            content='{"public_widget_id":"site_pub_abcdefghijklmnop"}',
        )
        token = bootstrap_response.json()["session_token"]
        chat_response = test_client.post(
            "/v1/public-widget/chat",
            headers=headers,
            content=(
                '{"session_token":"'
                + token
                + '","request_id":"request-1","message":"如何使用产品？"}'
            ),
        )
        messages_response = test_client.post(
            "/v1/public-widget/messages",
            headers=headers,
            content=(
                '{"session_token":"'
                + token
                + '","conversation_id":"'
                + chat_response.json()["conversation_id"]
                + '"}'
            ),
        )

    assert bootstrap_response.status_code == 200
    assert bootstrap_response.headers["access-control-allow-origin"] == origin
    assert "access-control-allow-credentials" not in bootstrap_response.headers
    assert chat_response.status_code == 200
    assert chat_response.json()["kind"] == "answer"
    assert messages_response.status_code == 200
    assert messages_response.json()["items"] == []


def test_public_widget_rejects_unregistered_origin(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    with client(monkeypatch) as test_client:
        response = test_client.post(
            "/v1/public-widget/bootstrap",
            headers={"Origin": "https://evil.example", "Content-Type": "text/plain"},
            content='{"public_widget_id":"site_pub_abcdefghijklmnop"}',
        )

    assert response.status_code == 403


def test_public_widget_rejects_known_crawlers_before_bootstrap(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    with client(monkeypatch) as test_client:
        response = test_client.post(
            "/v1/public-widget/bootstrap",
            headers={
                "Origin": "https://shop.example.com",
                "Content-Type": "text/plain",
                "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1)",
            },
            content='{"public_widget_id":"site_pub_abcdefghijklmnop"}',
        )

    assert response.status_code == 403


def test_public_widget_rejects_ai_crawlers_before_bootstrap(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    with client(monkeypatch) as test_client:
        response = test_client.post(
            "/v1/public-widget/bootstrap",
            headers={
                "Origin": "https://shop.example.com",
                "Content-Type": "text/plain",
                "User-Agent": "Mozilla/5.0 AppleWebKit/537.36 GPTBot/1.2",
            },
            content='{"public_widget_id":"site_pub_abcdefghijklmnop"}',
        )

    assert response.status_code == 403


def test_public_page_presence_uses_presence_only_token(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    headers = {
        "Origin": "https://shop.example.com",
        "Content-Type": "text/plain;charset=UTF-8",
        "User-Agent": "Mozilla/5.0 Chrome/126.0 Safari/537.36",
    }
    initial_payload = {
        "public_widget_id": "site_pub_abcdefghijklmnop",
        "visitor_id": "visitor-page-1",
        "event": "enter",
        "page_path": "/products/one",
        "page_kind": "checkout",
        "page_view_id": "page-view-1",
        "widget_state": "closed",
        "presence_source": "page_load",
        "runtime_version": "asset-build-123",
        "config_version": "widget-version-7",
        "connector_type": "wordpress",
        "connector_version": "0.4.0",
    }
    with client(monkeypatch) as test_client:
        initial = test_client.post(
            "/v1/public-widget/presence",
            headers=headers,
            content=json.dumps(initial_payload),
        )
        token = initial.json()["presence_token"]
        heartbeat_payload = {
            **{key: value for key, value in initial_payload.items() if key != "public_widget_id"},
            "presence_token": token,
            "event": "heartbeat",
        }
        heartbeat = test_client.post(
            "/v1/public-widget/presence",
            headers=headers,
            content=json.dumps(heartbeat_payload),
        )
        wrong_visitor = test_client.post(
            "/v1/public-widget/presence",
            headers=headers,
            content=json.dumps({**heartbeat_payload, "visitor_id": "visitor-page-2"}),
        )
        listed = test_client.get("/v1/admin/presence?active_within_seconds=60")

    assert initial.status_code == 200
    assert token
    assert initial.json()["presence_token_expires_at"]
    assert heartbeat.status_code == 200
    assert heartbeat.json()["presence_token"] == token
    assert wrong_visitor.status_code == 401
    assert [item["visitor_id"] for item in listed.json()["items"]] == ["visitor-page-1"]
    assert listed.json()["items"][0]["page_view_count"] == 1
    assert listed.json()["items"][0]["widget_state"] == "closed"
    assert listed.json()["items"][0]["page_kind"] == "product"
    assert listed.json()["items"][0]["intent_tier"] == "nurture"
    assert listed.json()["items"][0]["queue_eligible"] is False
    assert listed.json()["items"][0]["runtime_version"] == "asset-build-123"
    assert listed.json()["items"][0]["config_version"] == "widget-version-7"
    assert listed.json()["items"][0]["connector_type"] == "wordpress"
    assert listed.json()["items"][0]["connector_version"] == "0.4.0"


def test_public_presence_only_token_cannot_link_an_arbitrary_conversation(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    headers = {
        "Origin": "https://shop.example.com",
        "Content-Type": "text/plain;charset=UTF-8",
        "User-Agent": "Mozilla/5.0 Chrome/126.0 Safari/537.36",
    }
    initial_payload = {
        "public_widget_id": "site_pub_abcdefghijklmnop",
        "visitor_id": "visitor-unbound-1",
        "event": "enter",
        "page_path": "/products/one",
        "page_view_id": "page-view-unbound-1",
        "presence_source": "page_load",
    }
    with client(monkeypatch) as test_client:
        initial = test_client.post(
            "/v1/public-widget/presence",
            headers=headers,
            content=json.dumps(initial_payload),
        )
        forged = test_client.post(
            "/v1/public-widget/presence",
            headers=headers,
            content=json.dumps(
                {
                    "presence_token": initial.json()["presence_token"],
                    "visitor_id": "visitor-unbound-1",
                    "conversation_id": "conversation-from-another-session",
                    "event": "heartbeat",
                    "page_path": "/products/one",
                    "page_view_id": "page-view-unbound-1",
                    "presence_source": "page_load",
                }
            ),
        )
        listed = test_client.get("/v1/admin/presence?active_within_seconds=60")

    assert initial.status_code == 200
    assert forged.status_code == 422
    item = next(
        item for item in listed.json()["items"] if item["visitor_id"] == "visitor-unbound-1"
    )
    assert item["conversation_id"] is None


def test_session_bound_presence_links_only_its_own_existing_conversation(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    headers = {
        "Origin": "https://shop.example.com",
        "Content-Type": "text/plain;charset=UTF-8",
        "User-Agent": "Mozilla/5.0 Chrome/126.0 Safari/537.36",
    }
    with client(monkeypatch) as test_client:
        first_bootstrap = test_client.post(
            "/v1/public-widget/bootstrap",
            headers=headers,
            content='{"public_widget_id":"site_pub_abcdefghijklmnop"}',
        )
        first_token = first_bootstrap.json()["session_token"]
        chat = test_client.post(
            "/v1/public-widget/chat",
            headers=headers,
            content=json.dumps(
                {
                    "session_token": first_token,
                    "request_id": "presence-link-chat-1",
                    "message": "How much is this product?",
                }
            ),
        )
        conversation_id = chat.json()["conversation_id"]
        linked = test_client.post(
            "/v1/public-widget/presence",
            headers=headers,
            content=json.dumps(
                {
                    "session_token": first_token,
                    "visitor_id": "visitor-session-bound",
                    "conversation_id": conversation_id,
                    "event": "enter",
                    "page_path": "/products/widget",
                    "page_view_id": "page-session-bound",
                    "presence_source": "page_load",
                }
            ),
        )

        second_bootstrap = test_client.post(
            "/v1/public-widget/bootstrap",
            headers=headers,
            content='{"public_widget_id":"site_pub_abcdefghijklmnop"}',
        )
        cross_session = test_client.post(
            "/v1/public-widget/presence",
            headers=headers,
            content=json.dumps(
                {
                    "session_token": second_bootstrap.json()["session_token"],
                    "visitor_id": "visitor-cross-session",
                    "conversation_id": conversation_id,
                    "event": "enter",
                    "page_path": "/checkout",
                    "page_view_id": "page-cross-session",
                    "presence_source": "page_load",
                }
            ),
        )
        unknown_conversation = test_client.post(
            "/v1/public-widget/presence",
            headers=headers,
            content=json.dumps(
                {
                    "session_token": first_token,
                    "visitor_id": "visitor-unknown-conversation",
                    "conversation_id": "conversation-does-not-exist",
                    "event": "enter",
                    "page_path": "/checkout",
                    "page_view_id": "page-unknown-conversation",
                    "presence_source": "page_load",
                }
            ),
        )
        listed = test_client.get("/v1/admin/presence?active_within_seconds=60")

    assert first_bootstrap.status_code == 200
    assert chat.status_code == 200
    assert linked.status_code == 200
    assert cross_session.status_code == 422
    assert unknown_conversation.status_code == 422
    assert [item["visitor_id"] for item in listed.json()["items"]] == ["visitor-session-bound"]
    assert listed.json()["items"][0]["conversation_id"] == conversation_id
    assert listed.json()["items"][0]["session_active_dwell_seconds"] == 0


def test_public_widget_assets_are_served_without_site_secret(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    with client(monkeypatch) as test_client:
        javascript = test_client.get("/widget.js")
        assert javascript.headers["cache-control"] == "no-cache"
        stylesheet = test_client.get("/widget.css")
        runtime = test_client.get("/widget-runtime.js")

        versioned_javascript = test_client.get(f"/widget.js?v={widget_asset_version()}")

    assert javascript.status_code == 200
    assert "widget-runtime.js" in javascript.text
    assert "public_widget_id" in runtime.text
    assert runtime.status_code == 200
    assert "pollHumanMessages" in runtime.text
    assert "X-Agent-Site-Key" not in javascript.text
    assert stylesheet.status_code == 200
    assert versioned_javascript.headers["cache-control"] == ("public, max-age=31536000, immutable")


def test_widget_chat_rejects_invalid_site_credential(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    with client(monkeypatch) as test_client:
        response = test_client.post(
            "/v1/widget/chat",
            headers={"X-Agent-Site-Key": "invalid-site-key"},
            json={"message": "如何使用产品？"},
        )

    assert response.status_code == 401
    assert "site credential" in response.json()["detail"]


def test_widget_cannot_supply_tenant_or_customer_identity(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    with client(monkeypatch) as test_client:
        response = test_client.post(
            "/v1/widget/chat",
            headers={"X-Agent-Site-Key": "test-widget-site-key"},
            json={
                "message": "如何使用产品？",
                "tenant_id": "attacker-tenant",
                "customer_id": "attacker-customer",
            },
        )

    assert response.status_code == 422


def test_widget_order_query_routes_directly_to_human(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    with client(monkeypatch) as test_client:
        response = test_client.post(
            "/v1/widget/chat",
            headers={"X-Agent-Site-Key": "test-widget-site-key"},
            json={"message": "查询订单状态 DEMO-ORDER-1001"},
        )

    assert response.status_code == 200
    assert response.json()["kind"] == "handoff"
    assert "人工订单客服" in response.json()["message"]


def test_disabled_admin_auth_blocks_admin_api_but_not_widget(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    with client(monkeypatch) as test_client:
        test_client.app.state.container.authentication = DisabledAuthenticationAdapter()
        admin_response = test_client.post("/v1/chat", json={"message": "如何使用产品？"})
        widget_response = test_client.post(
            "/v1/widget/chat",
            headers={"X-Agent-Site-Key": "test-widget-site-key"},
            json={"message": "如何使用产品？"},
        )

    assert admin_response.status_code == 401
    assert widget_response.status_code == 200


def test_widget_presence_uses_trusted_site_mapping(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    with client(monkeypatch) as test_client:
        recorded = test_client.post(
            "/v1/widget/presence",
            headers={
                "X-Agent-Site-Key": "test-widget-site-key",
                "X-Agent-Visitor-IP": "203.0.113.24",
                "X-Agent-Visitor-Country": "ro",
                "X-Agent-Visitor-User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36"
                ),
            },
            json={
                "visitor_id": "visitor-1",
                "conversation_id": "conversation-1",
                "page_path": "/products/widget",
                "page_title": "Widget product",
                "referrer": "https://www.google.com/search?q=widget",
                "language": "ro-RO",
                "timezone": "Europe/Bucharest",
            },
        )
        listed = test_client.get("/v1/admin/presence?active_within_seconds=45")

    assert recorded.status_code == 200
    assert listed.status_code == 200
    assert len(listed.json()["items"]) == 1
    item = listed.json()["items"][0]
    assert {
        "site_id": item["site_id"],
        "visitor_id": item["visitor_id"],
        "conversation_id": item["conversation_id"],
        "page_path": item["page_path"],
        "last_seen_at": item["last_seen_at"],
        "first_seen_at": item["first_seen_at"],
        "page_title": item["page_title"],
        "referrer": item["referrer"],
        "ip_address": item["ip_address"],
        "country_code": item["country_code"],
        "browser": item["browser"],
        "operating_system": item["operating_system"],
        "device_type": item["device_type"],
        "language": item["language"],
        "timezone": item["timezone"],
        "page_view_count": item["page_view_count"],
        "session_started_at": item["session_started_at"],
        "current_page_entered_at": item["current_page_entered_at"],
        "last_page_view_id": item["last_page_view_id"],
        "widget_state": item["widget_state"],
        "presence_source": item["presence_source"],
    } == {
        "site_id": "site-a",
        "visitor_id": "visitor-1",
        "conversation_id": "conversation-1",
        "page_path": "/products/widget",
        "last_seen_at": recorded.json()["last_seen_at"],
        "first_seen_at": recorded.json()["last_seen_at"],
        "page_title": "Widget product",
        "referrer": "https://www.google.com/search?q=widget",
        "ip_address": "203.0.113.24",
        "country_code": "RO",
        "browser": "Chrome",
        "operating_system": "Windows",
        "device_type": "桌面设备",
        "language": "ro-RO",
        "timezone": "Europe/Bucharest",
        "page_view_count": 1,
        "session_started_at": recorded.json()["last_seen_at"],
        "current_page_entered_at": recorded.json()["last_seen_at"],
        "last_page_view_id": None,
        "widget_state": "open",
        "presence_source": "widget",
    }
    # Older connector requests do not provide a trusted page taxonomy. The
    # URL fallback may rank the visit, but must not be treated as a strong
    # connector-authenticated commercial signal.
    assert item["commercial_intent"] == 20
    assert item["page_kind"] == "product"
    assert item["intent_tier"] == "nurture"
    assert item["operation_priority"] == "P1"
    assert item["confidence_grade"] == "B"
    assert item["queue_eligible"] is False
    assert item["next_action"] == "continue_conversation"
    assert item["freshness"] == "current"
    assert item["rule_version"] == "lead-scoring.v1"
    assert item["scored_at"]
    assert item["current_page_dwell_seconds"] == 0
    assert "product_page" in item["data_coverage"]


def test_widget_presence_rejects_invalid_site_key(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    with client(monkeypatch) as test_client:
        response = test_client.post(
            "/v1/widget/presence",
            headers={"X-Agent-Site-Key": "invalid-site-key"},
            json={"visitor_id": "visitor-1", "page_path": "/"},
        )

    assert response.status_code == 401
