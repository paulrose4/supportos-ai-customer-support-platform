from dataclasses import dataclass

from redis.asyncio import Redis

from app.application.dto import SystemStatusConfiguration, WebCrawlPreflightRuntimePolicy
from app.application.services import (
    AdminSessionService,
    AdminUserManagementService,
    AnswerKnowledgeService,
    AuditLogService,
    CheckReadinessService,
    CreateHandoffService,
    CustomerExperienceService,
    EmailIdentityService,
    ExternalIdentityService,
    GetWebSyncCapabilityService,
    HandleChatService,
    InvitationManagementService,
    KnowledgeConsistencyAuditService,
    ListHandoffsService,
    PlatformIdentityService,
    ProductCatalogAnswerService,
    ProductRecommendationService,
    PublicWidgetSessionService,
    QueryBusinessDataService,
    RenderCustomerResponseService,
    ResponseRepairService,
    RetrievalPlanningService,
    SelfServiceEnrollmentEmailWorker,
    SelfServiceTenantProvisioningService,
    SemanticResponseReviewer,
    SiteAdministrationService,
    SiteKnowledgeReadinessService,
    SiteWebSourceConfigService,
    SkeletonResponseService,
    SupportAnalyticsService,
    SupportOperationsService,
    SystemStatusService,
    TenantExperienceLearningService,
    TenantExperienceService,
    TurnUnderstandingService,
    VisitorPresenceService,
    WebCrawlPreflightService,
    WebSyncJobService,
    WidgetAssetService,
)
from app.config import Settings
from app.domain.ports import (
    AuthenticationPort,
    ChatModelPort,
    EmbeddingProviderPort,
    PublicWidgetEventPort,
    RealtimeHubPort,
    RequestMetricsPort,
    WidgetSiteAuthenticationPort,
)
from app.graphs import LangGraphAgentRunner, build_customer_support_graph
from app.integrations.auth import (
    CompositeWidgetSiteAuthenticationAdapter,
    DingTalkIdentityProvider,
    DisabledAuthenticationAdapter,
    HmacPublicWidgetCursorAdapter,
    HmacPublicWidgetTokenAdapter,
    MockAuthenticationAdapter,
    PostgreSQLWidgetSiteAuthenticationAdapter,
    ScryptPasswordHasher,
    StaticWidgetSiteAuthenticationAdapter,
)
from app.integrations.cache import RedisPublicWidgetAccessCache
from app.integrations.filesystem import FileBackupStatusAdapter, FileSystemWidgetAssetStorage
from app.integrations.llm import (
    FakeChatModel,
    FakeEmbeddingProvider,
    FastEmbedEmbeddingProvider,
    InstrumentedChatModel,
    OpenAICandidateReviewer,
    OpenAIChatModelAdapter,
    OpenAIEmbeddingProvider,
    OpenAIRecommendationPlanner,
    RedisModelGateway,
)
from app.integrations.notifications import (
    SmtpHandoffNotificationAdapter,
    SmtpTransactionalEmailAdapter,
)
from app.integrations.object_storage import S3WidgetAssetStorage
from app.integrations.postgres import (
    DatabaseSessionManager,
    PostgreSQLAdminIdentityStore,
    PostgreSQLAuditLogAdapter,
    PostgreSQLBusinessReadAdapter,
    PostgreSQLConversationPersistenceAdapter,
    PostgreSQLCustomerExperienceAdapter,
    PostgreSQLEmailIdentityStore,
    PostgreSQLKnowledgeControlPlaneAdapter,
    PostgreSQLKnowledgeSchemaDependency,
    PostgreSQLMockBusinessDataSeeder,
    PostgreSQLOrderReadAdapter,
    PostgreSQLProductCatalogAdapter,
    PostgreSQLPublicWidgetAccessAdapter,
    PostgreSQLQueueHandoffAdapter,
    PostgreSQLSelfServiceOnboardingStore,
    PostgreSQLSiteAdministrationAdapter,
    PostgreSQLSiteWebSourceConfigStore,
    PostgreSQLSupportAnalyticsAdapter,
    PostgreSQLSupportOperationsAdapter,
    PostgreSQLSupportTicketReadAdapter,
    PostgreSQLTenantExperienceAdapter,
    PostgreSQLWebCrawlManifestStore,
    PostgreSQLWebSyncJobStore,
    PostgreSQLWidgetAssetRepository,
    PostgreSQLWorkforceIdentityStore,
)
from app.integrations.postgres.web_sync_workers import (
    PostgreSQLActiveTenantCatalog,
    PostgreSQLWebSyncWorkerRegistry,
)
from app.integrations.presence import InMemoryVisitorPresenceStore, RedisVisitorPresenceStore
from app.integrations.qdrant import QdrantKnowledgeAdapter, QdrantProductRecommendationAdapter
from app.integrations.rate_limit import (
    InMemoryWidgetCapacityAdapter,
    InMemoryWidgetRateLimitAdapter,
    RedisWidgetCapacityAdapter,
    RedisWidgetRateLimitAdapter,
)
from app.integrations.redis_backend import RedisDependency
from app.integrations.redis_knowledge_lease import RedisKnowledgeSyncLease
from app.integrations.retrieval import (
    DeterministicKnowledgeReranker,
    FastEmbedProductReranker,
    HashingSparseEmbeddingProvider,
    SitePublicationGatedKnowledgeRetriever,
)
from app.integrations.site_verification import SiteVerificationProbe
from app.knowledge import (
    GTranslateWebCrawlPreflightInspector,
    InMemoryKnowledgeSyncLease,
    KnowledgeSyncService,
    MarkdownChunker,
    MarkdownKnowledgeParser,
    ObsidianVaultScanner,
    SafeHttpFetcher,
    TenantObsidianVaultScanner,
    WebKnowledgeSyncService,
    WebsiteKnowledgeCrawler,
    WebSyncJobWorker,
)
from app.knowledge.web.execution_budget import WebSyncExecutionBudget
from app.observability import InMemoryRequestMetrics, PrometheusRequestMetrics
from app.realtime import InMemoryRealtimeHub, RedisPublicWidgetEventHub, RedisRealtimeHub


@dataclass(slots=True)
class Container:
    settings: Settings
    authentication: AuthenticationPort
    widget_authentication: WidgetSiteAuthenticationPort
    chat_service: HandleChatService
    readiness_service: CheckReadinessService
    list_handoffs_service: ListHandoffsService
    support_operations_service: SupportOperationsService
    visitor_presence_service: VisitorPresenceService
    realtime_hub: RealtimeHubPort
    knowledge_sync_service: KnowledgeSyncService
    web_knowledge_sync_service: WebKnowledgeSyncService
    web_sync_job_service: WebSyncJobService
    web_crawl_preflight_service: WebCrawlPreflightService
    web_sync_job_worker: WebSyncJobWorker
    database: DatabaseSessionManager
    knowledge_adapter: QdrantKnowledgeAdapter
    knowledge_consistency_audit_service: KnowledgeConsistencyAuditService
    chat_model: ChatModelPort
    embedding_provider: EmbeddingProviderPort
    product_reranker: FastEmbedProductReranker | None
    product_recommendation_index: QdrantProductRecommendationAdapter | None
    mock_business_seeder: PostgreSQLMockBusinessDataSeeder
    admin_user_management_service: AdminUserManagementService
    audit_log_service: AuditLogService
    site_administration_service: SiteAdministrationService
    site_web_source_config_service: SiteWebSourceConfigService
    system_status_service: SystemStatusService
    support_analytics_service: SupportAnalyticsService
    customer_experience_service: CustomerExperienceService
    widget_asset_service: WidgetAssetService
    site_knowledge_readiness_service: SiteKnowledgeReadinessService
    tenant_experience_service: TenantExperienceService
    tenant_experience_learning_service: TenantExperienceLearningService
    request_metrics: RequestMetricsPort
    redis_backend: RedisDependency | None = None
    admin_session_service: AdminSessionService | None = None
    external_identity_service: ExternalIdentityService | None = None
    email_identity_service: EmailIdentityService | None = None
    invitation_management_service: InvitationManagementService | None = None
    platform_identity_service: PlatformIdentityService | None = None
    global_knowledge_sync_service: KnowledgeSyncService | None = None
    public_widget_session_service: PublicWidgetSessionService | None = None
    self_service_onboarding_service: SelfServiceTenantProvisioningService | None = None
    enrollment_email_worker: SelfServiceEnrollmentEmailWorker | None = None
    public_widget_event_hub: PublicWidgetEventPort | None = None
    web_sync_tenant_catalog: PostgreSQLActiveTenantCatalog | None = None
    web_sync_worker_registry: PostgreSQLWebSyncWorkerRegistry | None = None
    web_sync_execution_budget: WebSyncExecutionBudget | None = None
    web_sync_capability_service: GetWebSyncCapabilityService | None = None


async def build_container(settings: Settings) -> Container:
    if settings.auth_mode not in {"mock", "disabled", "session"}:
        raise NotImplementedError("unsupported administrative authentication mode")
    database = DatabaseSessionManager(
        settings.database_url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout_seconds=settings.database_pool_timeout_seconds,
        pool_recycle_seconds=settings.database_pool_recycle_seconds,
        prepared_statement_cache_size=settings.database_prepared_statement_cache_size,
    )
    redis_backend = (
        RedisDependency(Redis.from_url(settings.redis_url, decode_responses=True))
        if settings.redis_url
        else None
    )
    request_metrics: RequestMetricsPort = (
        PrometheusRequestMetrics()
        if settings.prometheus_metrics_enabled
        else InMemoryRequestMetrics()
    )
    authentication: AuthenticationPort
    admin_session_service: AdminSessionService | None = None
    identity_store = PostgreSQLAdminIdentityStore(database.session_factory)
    workforce_identity_store = PostgreSQLWorkforceIdentityStore(database.session_factory)
    email_identity_store = PostgreSQLEmailIdentityStore(database.session_factory)
    onboarding_store = PostgreSQLSelfServiceOnboardingStore(database.session_factory)
    platform_identity_service = PlatformIdentityService(
        workforce_identity_store,
        workforce_identity_store,
    )
    password_hasher = ScryptPasswordHasher()
    transactional_email = None
    if settings.transactional_email_enabled:
        transactional_email = SmtpTransactionalEmailAdapter(
            host=settings.smtp_host or "",
            port=settings.smtp_port,
            from_address=settings.smtp_from_address or "",
            username=settings.smtp_username,
            password=(
                settings.smtp_password.get_secret_value()
                if settings.smtp_password is not None
                else None
            ),
            use_starttls=settings.smtp_use_starttls,
        )
    email_identity_service = None
    invitation_management_service = None
    if settings.email_login_enabled:
        email_identity_service = EmailIdentityService(
            store=email_identity_store,
            password_hasher=password_hasher,
            session_ttl_seconds=settings.admin_session_ttl_seconds,
            completion_grant_ttl_seconds=settings.login_completion_grant_ttl_seconds,
            password_reset_ttl_seconds=settings.password_reset_ttl_seconds,
            public_base_url=settings.admin_public_base_url,
            transactional_email=transactional_email,
            login_max_attempts=settings.admin_login_max_attempts,
            login_window_seconds=settings.admin_login_window_seconds,
            login_lockout_seconds=settings.admin_login_lockout_seconds,
        )
    if settings.invite_registration_enabled:
        invitation_management_service = InvitationManagementService(
            store=email_identity_store,
            public_base_url=settings.admin_public_base_url,
            transactional_email=transactional_email,
        )
    rate_limits = (
        RedisWidgetRateLimitAdapter(
            redis_backend.client,
            fingerprint_secret=settings.enrollment_token_secret.get_secret_value(),
        )
        if redis_backend is not None
        else InMemoryWidgetRateLimitAdapter(
            fingerprint_secret=settings.enrollment_token_secret.get_secret_value()
        )
    )
    self_service_onboarding_service = SelfServiceTenantProvisioningService(
        store=onboarding_store,
        identity_store=email_identity_store,
        password_hasher=password_hasher,
        rate_limits=rate_limits,
        token_secret=settings.enrollment_token_secret.get_secret_value(),
        public_base_url=settings.admin_public_base_url,
        intent_ttl_seconds=settings.enrollment_intent_ttl_seconds,
        verification_ttl_seconds=settings.enrollment_verification_ttl_seconds,
    )
    enrollment_email_worker = None
    if settings.employee_enrollment_enabled and transactional_email is not None:
        enrollment_email_worker = SelfServiceEnrollmentEmailWorker(
            store=onboarding_store,
            transactional_email=transactional_email,
            token_secret=settings.enrollment_token_secret.get_secret_value(),
            public_base_url=settings.admin_public_base_url,
            poll_seconds=settings.enrollment_email_poll_seconds,
            lease_seconds=settings.enrollment_email_lease_seconds,
            max_attempts=settings.enrollment_max_email_attempts,
        )
    admin_user_management_service = AdminUserManagementService(
        identity_store=identity_store,
        password_hasher=password_hasher,
    )
    audit_log_service = AuditLogService(PostgreSQLAuditLogAdapter(database.session_factory))
    support_analytics_service = SupportAnalyticsService(
        PostgreSQLSupportAnalyticsAdapter(database.session_factory)
    )
    widget_asset_repository = PostgreSQLWidgetAssetRepository(database.session_factory)
    if settings.widget_asset_storage_backend == "s3":
        widget_asset_storage = S3WidgetAssetStorage(
            bucket=settings.widget_asset_s3_bucket or "",
            prefix=settings.widget_asset_s3_prefix,
            endpoint_url=settings.widget_asset_s3_endpoint_url,
            region_name=settings.widget_asset_s3_region,
            access_key_id=settings.widget_asset_s3_access_key_id,
            secret_access_key=(
                settings.widget_asset_s3_secret_access_key.get_secret_value()
                if settings.widget_asset_s3_secret_access_key
                else None
            ),
        )
    else:
        widget_asset_storage = FileSystemWidgetAssetStorage(settings.widget_asset_storage_path)
    widget_asset_service = WidgetAssetService(
        repository=widget_asset_repository,
        storage=widget_asset_storage,
        maximum_upload_bytes=settings.widget_asset_max_upload_bytes,
    )
    customer_experience_service = CustomerExperienceService(
        PostgreSQLCustomerExperienceAdapter(database.session_factory),
        widget_asset_repository,
    )
    if settings.auth_mode == "mock":
        authentication = MockAuthenticationAdapter(
            subject_id=settings.mock_subject_id,
            tenant_id=settings.default_tenant_id,
        )
    else:
        authentication = DisabledAuthenticationAdapter()
    if settings.auth_mode == "session":
        admin_session_service = AdminSessionService(
            identity_store=identity_store,
            password_hasher=password_hasher,
            session_ttl_seconds=settings.admin_session_ttl_seconds,
            login_max_attempts=settings.admin_login_max_attempts,
            login_window_seconds=settings.admin_login_window_seconds,
            login_lockout_seconds=settings.admin_login_lockout_seconds,
        )
    external_identity_service: ExternalIdentityService | None = None
    external_providers = []
    if "dingtalk" in settings.external_login_providers:
        external_providers.append(
            DingTalkIdentityProvider(
                client_id=settings.dingtalk_client_id or "",
                client_secret=(
                    settings.dingtalk_client_secret.get_secret_value()
                    if settings.dingtalk_client_secret is not None
                    else ""
                ),
                organization_id=settings.dingtalk_organization_id or "",
                timeout_seconds=settings.dingtalk_timeout_seconds,
            )
        )
    if external_providers or settings.email_login_enabled:
        external_identity_service = ExternalIdentityService(
            providers=tuple(external_providers),
            identity_store=workforce_identity_store,
            session_ttl_seconds=settings.admin_session_ttl_seconds,
            login_state_ttl_seconds=settings.external_login_state_ttl_seconds,
            completion_grant_ttl_seconds=settings.login_completion_grant_ttl_seconds,
        )
    if redis_backend is not None:
        realtime_hub: RealtimeHubPort = RedisRealtimeHub(redis_backend.client)
        presence_store = RedisVisitorPresenceStore(
            redis_backend.client,
            ttl_seconds=settings.redis_presence_ttl_seconds,
        )
        rate_limit_adapter = RedisWidgetRateLimitAdapter(
            redis_backend.client,
            fingerprint_secret=settings.widget_token_secret.get_secret_value(),
        )
        capacity_adapter = RedisWidgetCapacityAdapter(
            redis_backend.client,
            global_limit=settings.widget_chat_global_concurrency,
            site_limit=settings.widget_chat_site_concurrency,
            wait_seconds=settings.widget_chat_capacity_wait_seconds,
            lease_seconds=settings.widget_chat_capacity_lease_seconds,
        )
        knowledge_sync_lease = RedisKnowledgeSyncLease(redis_backend.client)
        public_widget_event_hub: PublicWidgetEventPort | None = (
            RedisPublicWidgetEventHub(redis_backend.client) if settings.widget_sse_enabled else None
        )
    else:
        realtime_hub = InMemoryRealtimeHub()
        presence_store = InMemoryVisitorPresenceStore()
        rate_limit_adapter = InMemoryWidgetRateLimitAdapter(
            fingerprint_secret=settings.widget_token_secret.get_secret_value(),
        )
        capacity_adapter = InMemoryWidgetCapacityAdapter(
            global_limit=settings.widget_chat_global_concurrency,
            site_limit=settings.widget_chat_site_concurrency,
            wait_seconds=settings.widget_chat_capacity_wait_seconds,
        )
        knowledge_sync_lease = InMemoryKnowledgeSyncLease()
        public_widget_event_hub = None
    conversation_adapter = PostgreSQLConversationPersistenceAdapter(
        database.session_factory,
        auto_resolution_hours=settings.auto_resolution_inactivity_hours,
    )
    visitor_presence_service = VisitorPresenceService(
        presence_store,
        conversation_context=conversation_adapter,
        conversation_lead_context=conversation_adapter,
    )
    direct_event_publisher = realtime_hub if redis_backend is None else None
    handoff_adapter = PostgreSQLQueueHandoffAdapter(database.session_factory)
    handoff_notification = None
    if settings.handoff_email_enabled:
        handoff_notification = SmtpHandoffNotificationAdapter(
            host=settings.smtp_host or "",
            port=settings.smtp_port,
            from_address=settings.smtp_from_address or "",
            recipients_by_site=settings.handoff_email_recipients,
            username=settings.smtp_username,
            password=(
                settings.smtp_password.get_secret_value()
                if settings.smtp_password is not None
                else None
            ),
            use_starttls=settings.smtp_use_starttls,
        )
    support_operations_adapter = PostgreSQLSupportOperationsAdapter(database.session_factory)
    site_administration_service = SiteAdministrationService(
        PostgreSQLSiteAdministrationAdapter(database.session_factory),
        default_daily_message_limit=settings.widget_default_daily_message_limit,
        verification_token_secret=settings.enrollment_token_secret.get_secret_value(),
        verification_ttl_seconds=settings.site_verification_ttl_seconds,
        verification_probe=SiteVerificationProbe(),
    )
    site_web_source_config_service = SiteWebSourceConfigService(
        PostgreSQLSiteWebSourceConfigStore(database.session_factory)
    )
    widget_token_secret = settings.widget_token_secret.get_secret_value()
    public_widget_access = PostgreSQLPublicWidgetAccessAdapter(database.session_factory)
    cached_public_widget_access = (
        RedisPublicWidgetAccessCache(
            public_widget_access,
            redis_backend.client,
            ttl_seconds=settings.widget_site_cache_ttl_seconds,
            negative_ttl_seconds=settings.widget_site_negative_cache_ttl_seconds,
            fallback_concurrency=settings.widget_site_db_fallback_concurrency,
            metrics=request_metrics,
        )
        if redis_backend is not None
        else public_widget_access
    )
    public_widget_session_service = PublicWidgetSessionService(
        access=cached_public_widget_access,
        tokens=HmacPublicWidgetTokenAdapter(
            secret=widget_token_secret,
            ttl_seconds=settings.widget_token_ttl_seconds,
        ),
        rate_limits=rate_limit_adapter,
        bootstrap_limit_per_minute=settings.widget_bootstrap_rate_limit_per_minute,
        chat_limit_per_minute=settings.widget_chat_rate_limit_per_minute,
        presence_limit_per_minute=settings.widget_presence_rate_limit_per_minute,
        presence_source_limit_per_minute=(settings.widget_presence_source_rate_limit_per_minute),
        presence_site_limit_per_minute=settings.widget_presence_site_rate_limit_per_minute,
        sessions=public_widget_access,
        resume_ttl_seconds=settings.widget_resume_token_ttl_seconds,
        capacity=capacity_adapter,
        metrics=request_metrics,
    )
    knowledge_control_plane = PostgreSQLKnowledgeControlPlaneAdapter(database.session_factory)
    product_catalog = PostgreSQLProductCatalogAdapter(database.session_factory)
    site_knowledge_readiness_service = SiteKnowledgeReadinessService(
        catalog=product_catalog,
        control_plane=knowledge_control_plane,
    )
    business_delegate = PostgreSQLBusinessReadAdapter(database.session_factory)
    business_query_service = QueryBusinessDataService(
        orders=PostgreSQLOrderReadAdapter(business_delegate),
        support_tickets=PostgreSQLSupportTicketReadAdapter(business_delegate),
    )
    chat_model, embedding_provider = _build_model_adapters(settings)
    if (
        settings.model_gateway_enabled
        and settings.llm_provider == "openai"
        and redis_backend is not None
    ):
        chat_model = RedisModelGateway(
            chat_model,
            redis_backend.client,
            requests_per_minute=settings.model_gateway_requests_per_minute,
            tokens_per_minute=settings.model_gateway_tokens_per_minute,
            reserved_output_tokens=settings.model_gateway_reserved_output_tokens,
            circuit_failure_threshold=settings.model_circuit_failure_threshold,
            circuit_recovery_seconds=settings.model_circuit_recovery_seconds,
        )
    chat_model = InstrumentedChatModel(chat_model, request_metrics)
    tenant_experience_adapter = PostgreSQLTenantExperienceAdapter(
        database.session_factory,
        embedding_provider,
        embedding_dimension=settings.tenant_experience_embedding_dimension,
        minimum_similarity=settings.tenant_experience_minimum_similarity,
        retrieval_timeout_seconds=settings.tenant_experience_retrieval_timeout_seconds,
        case_ttl_days=settings.tenant_experience_case_ttl_days,
        outcome_observation_days=settings.tenant_experience_outcome_observation_days,
    )
    tenant_experience_service = TenantExperienceService(
        tenant_experience_adapter,
        enabled=settings.tenant_experience_enabled,
        release_mode=settings.tenant_experience_release_mode,
        release_version=settings.tenant_experience_release_version,
        rollout_percent=settings.tenant_experience_rollout_percent,
        maximum_online_risk=settings.tenant_experience_maximum_online_risk,
        result_limit=settings.tenant_experience_result_limit,
    )
    tenant_experience_learning_service = TenantExperienceLearningService(tenant_experience_adapter)
    sparse_embedding_provider = HashingSparseEmbeddingProvider()
    knowledge_reranker = DeterministicKnowledgeReranker()
    knowledge_adapter = QdrantKnowledgeAdapter(
        url=settings.qdrant_url,
        trust_env=settings.qdrant_trust_env,
        collection_name=settings.qdrant_collection,
        embedding_provider=embedding_provider,
        sparse_embedding_provider=sparse_embedding_provider,
        reranker=knowledge_reranker,
        vector_size=settings.embedding_dimension,
        tenant_candidate_limit=settings.hybrid_tenant_candidate_limit,
        global_candidate_limit=settings.hybrid_global_candidate_limit,
        rerank_candidate_limit=settings.hybrid_rerank_candidate_limit,
        tenant_weight=settings.hybrid_tenant_weight,
        global_weight=settings.hybrid_global_weight,
        maintenance_timeout_seconds=settings.qdrant_maintenance_timeout_seconds,
    )
    online_knowledge_retriever = SitePublicationGatedKnowledgeRetriever(
        knowledge_adapter,
        knowledge_control_plane,
    )
    knowledge_consistency_audit_service = KnowledgeConsistencyAuditService(
        control_plane=knowledge_control_plane,
        indexer=knowledge_adapter,
    )
    retrieval_planner = RetrievalPlanningService(
        enabled=settings.retrieval_plan_enabled,
        query_enhancement_enabled=settings.retrieval_query_enhancement_enabled,
    )
    product_recommendation_index = (
        QdrantProductRecommendationAdapter(
            url=settings.qdrant_url,
            trust_env=settings.qdrant_trust_env,
            collection_name=settings.product_recommendation_collection,
            embedding_provider=embedding_provider,
            sparse_embedding_provider=sparse_embedding_provider,
            vector_size=settings.embedding_dimension,
        )
        if settings.product_recommendation_enabled and settings.product_recommendation_index_enabled
        else None
    )
    product_reranker = (
        FastEmbedProductReranker(
            model_name=settings.product_recommendation_reranker_model,
            cache_dir=settings.product_recommendation_reranker_cache_dir,
            threads=settings.product_recommendation_reranker_threads,
            batch_size=settings.product_recommendation_reranker_batch_size,
            timeout_seconds=settings.product_recommendation_reranker_timeout_seconds,
        )
        if (
            settings.product_recommendation_enabled
            and settings.product_recommendation_reranker_enabled
        )
        else None
    )
    recommendation_planner = None
    candidate_reviewer = None
    recommendation_api_key = (
        settings.openai_api_key.get_secret_value() if settings.openai_api_key is not None else None
    )
    if (
        settings.product_recommendation_enabled
        and settings.product_recommendation_llm_planner_enabled
    ):
        if recommendation_api_key is None:
            raise ValueError("OPENAI_API_KEY is required for recommendation planning")
        recommendation_planner = OpenAIRecommendationPlanner(
            api_key=recommendation_api_key,
            model=settings.product_recommendation_llm_model,
            timeout_seconds=settings.product_recommendation_llm_timeout_seconds,
            base_url=settings.openai_base_url,
        )
    if (
        settings.product_recommendation_enabled
        and settings.product_recommendation_llm_review_enabled
    ):
        if recommendation_api_key is None:
            raise ValueError("OPENAI_API_KEY is required for candidate review")
        candidate_reviewer = OpenAICandidateReviewer(
            api_key=recommendation_api_key,
            model=settings.product_recommendation_llm_model,
            timeout_seconds=settings.product_recommendation_llm_timeout_seconds,
            base_url=settings.openai_base_url,
        )
    product_recommendation_service = ProductRecommendationService(
        product_catalog,
        embedding_provider=embedding_provider if settings.product_recommendation_enabled else None,
        reranker=product_reranker if settings.product_recommendation_enabled else None,
        planner=recommendation_planner if settings.product_recommendation_enabled else None,
        reviewer=candidate_reviewer if settings.product_recommendation_enabled else None,
        candidate_retriever=product_recommendation_index,
        dense_pool_size=settings.product_recommendation_dense_pool_size,
        rerank_pool_size=settings.product_recommendation_rerank_pool_size,
        minimum_score=settings.product_recommendation_min_score,
        specification_max_age_days=settings.product_specification_max_age_days,
        price_max_age_days=settings.product_price_max_age_days,
        stock_max_age_days=settings.product_stock_max_age_days,
    )
    knowledge_answer_service = AnswerKnowledgeService(
        retriever=online_knowledge_retriever,
        chat_model=chat_model,
        control_plane=knowledge_control_plane,
        product_catalog=ProductCatalogAnswerService(
            product_catalog,
            specification_max_age_days=settings.product_specification_max_age_days,
            price_max_age_days=settings.product_price_max_age_days,
            stock_max_age_days=settings.product_stock_max_age_days,
            recommendation_service=product_recommendation_service,
        ),
        retrieval_planner=retrieval_planner,
        relation_expansion_enabled=settings.retrieval_relation_expansion_enabled,
        # Shared deployments disable the local cache until the cache port is Redis-backed.
        answer_cache_ttl_seconds=(
            0 if redis_backend is not None else settings.faq_answer_cache_ttl_seconds
        ),
        answer_cache_max_entries=settings.faq_answer_cache_max_entries,
    )
    knowledge_sync_service = KnowledgeSyncService(
        scanner=TenantObsidianVaultScanner(settings.tenant_knowledge_root),
        parser=MarkdownKnowledgeParser(),
        chunker=MarkdownChunker(),
        embedding_provider=embedding_provider,
        sparse_embedding_provider=sparse_embedding_provider,
        indexer=knowledge_adapter,
        control_plane=knowledge_control_plane,
        lease=knowledge_sync_lease,
        lease_seconds=settings.knowledge_sync_lease_seconds,
        index_namespace=(f"{settings.qdrant_collection}:{settings.knowledge_index_schema_version}"),
    )
    global_knowledge_sync_service = KnowledgeSyncService(
        scanner=ObsidianVaultScanner(settings.global_vault_path),
        parser=MarkdownKnowledgeParser(),
        chunker=MarkdownChunker(),
        embedding_provider=embedding_provider,
        sparse_embedding_provider=sparse_embedding_provider,
        indexer=knowledge_adapter,
        control_plane=knowledge_control_plane,
        lease=knowledge_sync_lease,
        lease_seconds=settings.knowledge_sync_lease_seconds,
        index_namespace=(f"{settings.qdrant_collection}:{settings.knowledge_index_schema_version}"),
    )
    readiness_dependencies = {
        "postgres": database,
        "knowledge_schema": PostgreSQLKnowledgeSchemaDependency(database.engine),
        "qdrant": knowledge_adapter,
    }
    if redis_backend is not None:
        readiness_dependencies["redis"] = redis_backend
    readiness_service = CheckReadinessService(readiness_dependencies)
    system_status_service = SystemStatusService(
        readiness=readiness_service,
        metrics=request_metrics,
        backup_status=FileBackupStatusAdapter(settings.backup_status_directory),
        backup_max_age_hours=settings.backup_max_age_hours,
        configuration=SystemStatusConfiguration(
            app_env=settings.app_env,
            auth_mode=settings.auth_mode,
            llm_provider=settings.llm_provider,
            embedding_provider=settings.embedding_provider,
            realtime_backend="redis" if redis_backend is not None else "in_memory",
            presence_backend="redis" if redis_backend is not None else "in_memory",
            horizontal_scaling_ready=(
                redis_backend is not None and settings.prometheus_metrics_enabled
            ),
            build_sha=settings.build_sha,
            state_schema_version=10,
            chat_model=settings.openai_chat_model if settings.llm_provider == "openai" else "fake",
            turn_planner_prompt_version="turn-planner-v1",
            response_renderer_prompt_version="response-brief-v1",
        ),
    )
    web_fetcher = SafeHttpFetcher()
    web_knowledge_sync_service = WebKnowledgeSyncService(
        crawler=WebsiteKnowledgeCrawler(
            fetcher=web_fetcher,
            domain_concurrency=settings.web_sync_domain_concurrency,
        ),
        chunker=MarkdownChunker(),
        embedding_provider=embedding_provider,
        sparse_embedding_provider=sparse_embedding_provider,
        indexer=knowledge_adapter,
        control_plane=knowledge_control_plane,
        product_catalog=product_catalog,
        publication_committer=knowledge_control_plane,
        product_recommendation_index=product_recommendation_index,
        index_namespace=(f"{settings.qdrant_collection}:{settings.knowledge_index_schema_version}"),
    )
    web_crawl_manifest_store = PostgreSQLWebCrawlManifestStore(database.session_factory)
    web_crawl_preflight_service = WebCrawlPreflightService(
        inspector=GTranslateWebCrawlPreflightInspector(fetcher=web_fetcher),
        store=web_crawl_manifest_store,
        runtime_policy=WebCrawlPreflightRuntimePolicy(
            max_sitemaps=settings.web_crawler_preflight_max_sitemaps,
            max_response_bytes=settings.web_crawler_sitemap_max_response_bytes,
            max_decompressed_response_bytes=(
                settings.web_crawler_sitemap_max_decompressed_response_bytes
            ),
            max_compression_ratio=settings.web_crawler_max_compression_ratio,
            request_timeout_seconds=settings.web_crawler_request_timeout_seconds,
            max_urls=settings.web_crawler_manifest_safety_ceiling,
        ),
    )
    web_sync_job_store = PostgreSQLWebSyncJobStore(database.session_factory)
    web_sync_tenant_catalog = PostgreSQLActiveTenantCatalog(database.session_factory)
    web_sync_worker_registry = PostgreSQLWebSyncWorkerRegistry(database.session_factory)
    web_sync_capability_service = GetWebSyncCapabilityService(web_sync_worker_registry)
    web_sync_execution_budget = WebSyncExecutionBudget(
        page_slots=settings.web_sync_max_pages_in_flight,
        finalization_slots=settings.web_sync_max_finalizations,
    )
    web_sync_job_service = WebSyncJobService(
        web_sync_job_store,
        web_crawl_manifest_store,
        production_enabled=settings.web_crawler_production_sync_enabled,
        production_pipeline_ready=True,
        manifest_max_age_hours=settings.web_crawler_manifest_max_age_hours,
        manifest_safety_ceiling=settings.web_crawler_manifest_safety_ceiling,
        full_shadow_enabled=settings.web_crawler_full_shadow_enabled,
        knowledge_control_plane=knowledge_control_plane,
    )
    web_sync_job_worker = WebSyncJobWorker(
        store=web_sync_job_store,
        manifest_store=web_crawl_manifest_store,
        synchronizer=web_knowledge_sync_service,
        concurrency=min(
            settings.web_sync_worker_concurrency,
            settings.web_sync_max_pages_per_tenant,
        ),
        lease_seconds=settings.web_sync_worker_lease_seconds,
        heartbeat_seconds=settings.web_sync_worker_heartbeat_seconds,
        staging_retention_hours=settings.web_sync_staging_retention_hours,
        prepare_batch_size=settings.web_sync_prepare_batch_size,
        execution_budget=web_sync_execution_budget,
        max_no_progress_seconds=settings.web_sync_max_no_progress_seconds,
        finalization_timeout_seconds=settings.web_sync_finalization_timeout_seconds,
    )
    graph_runner = LangGraphAgentRunner(
        build_customer_support_graph(
            response_service=SkeletonResponseService(),
            handoff_service=CreateHandoffService(handoff_adapter, handoff_notification),
            knowledge_answer_service=knowledge_answer_service,
            business_query_service=business_query_service,
            response_repair_service=ResponseRepairService(chat_model),
            response_renderer_service=RenderCustomerResponseService(
                chat_model,
                semantic_reviewer=SemanticResponseReviewer(
                    chat_model,
                    mode=settings.semantic_reviewer_mode,
                ),
            ),
            turn_understanding_service=TurnUnderstandingService(
                chat_model,
                mode=settings.turn_planner_mode,
            ),
            tenant_experience_service=tenant_experience_service,
            metrics=request_metrics,
            deterministic_render_enabled=settings.deterministic_render_enabled,
        )
    )
    return Container(
        settings=settings,
        authentication=authentication,
        widget_authentication=CompositeWidgetSiteAuthenticationAdapter(
            (
                PostgreSQLWidgetSiteAuthenticationAdapter(database.session_factory),
                StaticWidgetSiteAuthenticationAdapter(settings.effective_widget_site_keys),
            )
        ),
        chat_service=HandleChatService(
            graph_runner,
            conversation_adapter,
            direct_event_publisher,
            conversation_adapter,
            conversation_adapter,
            request_metrics,
            HmacPublicWidgetCursorAdapter(secret=widget_token_secret),
        ),
        list_handoffs_service=ListHandoffsService(handoff_adapter),
        admin_user_management_service=admin_user_management_service,
        audit_log_service=audit_log_service,
        site_administration_service=site_administration_service,
        site_web_source_config_service=site_web_source_config_service,
        system_status_service=system_status_service,
        support_analytics_service=support_analytics_service,
        customer_experience_service=customer_experience_service,
        widget_asset_service=widget_asset_service,
        site_knowledge_readiness_service=site_knowledge_readiness_service,
        tenant_experience_service=tenant_experience_service,
        tenant_experience_learning_service=tenant_experience_learning_service,
        request_metrics=request_metrics,
        redis_backend=redis_backend,
        visitor_presence_service=visitor_presence_service,
        support_operations_service=SupportOperationsService(
            support_operations_adapter,
            direct_event_publisher,
            memory_candidate_workflow_enabled=settings.memory_candidate_workflow_enabled,
        ),
        realtime_hub=realtime_hub,
        readiness_service=readiness_service,
        knowledge_sync_service=knowledge_sync_service,
        web_knowledge_sync_service=web_knowledge_sync_service,
        web_sync_job_service=web_sync_job_service,
        web_crawl_preflight_service=web_crawl_preflight_service,
        web_sync_job_worker=web_sync_job_worker,
        web_sync_tenant_catalog=web_sync_tenant_catalog,
        web_sync_worker_registry=web_sync_worker_registry,
        web_sync_execution_budget=web_sync_execution_budget,
        web_sync_capability_service=web_sync_capability_service,
        database=database,
        knowledge_adapter=knowledge_adapter,
        knowledge_consistency_audit_service=knowledge_consistency_audit_service,
        chat_model=chat_model,
        embedding_provider=embedding_provider,
        product_reranker=product_reranker,
        product_recommendation_index=product_recommendation_index,
        mock_business_seeder=PostgreSQLMockBusinessDataSeeder(database.session_factory),
        admin_session_service=admin_session_service,
        external_identity_service=external_identity_service,
        email_identity_service=email_identity_service,
        invitation_management_service=invitation_management_service,
        platform_identity_service=platform_identity_service,
        global_knowledge_sync_service=global_knowledge_sync_service,
        public_widget_session_service=public_widget_session_service,
        self_service_onboarding_service=self_service_onboarding_service,
        enrollment_email_worker=enrollment_email_worker,
        public_widget_event_hub=public_widget_event_hub,
    )


def _build_model_adapters(settings: Settings) -> tuple[ChatModelPort, EmbeddingProviderPort]:
    api_key = (
        settings.openai_api_key.get_secret_value() if settings.openai_api_key is not None else None
    )
    if settings.llm_provider == "openai":
        if api_key is None:
            raise ValueError("OPENAI_API_KEY is required for the OpenAI chat adapter")
        chat_model: ChatModelPort = OpenAIChatModelAdapter(
            api_key=api_key,
            model=settings.openai_chat_model,
            timeout_seconds=settings.openai_timeout_seconds,
            base_url=settings.openai_base_url,
            api_mode=settings.openai_chat_api_mode,
        )
    else:
        chat_model = FakeChatModel()

    if settings.embedding_provider == "openai":
        if api_key is None:
            raise ValueError("OPENAI_API_KEY is required for the OpenAI embedding adapter")
        embedding_provider: EmbeddingProviderPort = OpenAIEmbeddingProvider(
            api_key=api_key,
            model=settings.openai_embedding_model,
            dimension=settings.embedding_dimension,
            timeout_seconds=settings.openai_timeout_seconds,
            base_url=settings.openai_base_url,
        )
    elif settings.embedding_provider == "fastembed":
        embedding_provider = FastEmbedEmbeddingProvider(
            model_name=settings.local_embedding_model,
            dimension=settings.embedding_dimension,
            cache_dir=settings.local_embedding_cache_dir,
            threads=settings.local_embedding_threads,
            batch_size=settings.local_embedding_batch_size,
            cache_max_entries=settings.local_embedding_cache_max_entries,
        )
    else:
        embedding_provider = FakeEmbeddingProvider()
    return chat_model, embedding_provider
