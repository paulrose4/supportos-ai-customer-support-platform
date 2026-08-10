from functools import lru_cache
from ipaddress import ip_network
from pathlib import Path
from urllib.parse import urlparse

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    app_name: str = "customer-support-agent"
    build_sha: str = "development"
    log_level: str = "INFO"
    auth_mode: str = "mock"
    email_login_enabled: bool = True
    invite_registration_enabled: bool = True
    self_service_signup_enabled: bool = False
    self_service_signup_mode: str = "provision_tenant"
    employee_enrollment_enabled: bool = False
    enrollment_token_secret: SecretStr = SecretStr(
        "development-only-enrollment-token-secret-change-before-production"
    )
    enrollment_intent_ttl_seconds: int = 1800
    enrollment_verification_ttl_seconds: int = 1800
    enrollment_email_poll_seconds: float = 5.0
    enrollment_email_lease_seconds: int = 120
    enrollment_max_email_attempts: int = 5
    trusted_proxy_cidrs: list[str] = []
    external_login_providers: list[str] = []
    dingtalk_login_enabled: bool = False
    legacy_login_enabled: bool = True
    mock_subject_id: str = "dev-customer"
    admin_session_cookie_name: str = "support_admin_session"
    login_completion_cookie_name: str = "support_login_completion"
    admin_session_ttl_seconds: int = 43200
    external_login_state_ttl_seconds: int = 300
    login_completion_grant_ttl_seconds: int = 300
    password_reset_ttl_seconds: int = 1800
    admin_login_max_attempts: int = 10
    admin_login_window_seconds: int = 900
    admin_login_lockout_seconds: int = 900
    admin_cookie_secure: bool = False
    admin_public_base_url: str = "http://localhost:8000"
    dingtalk_client_id: str | None = None
    dingtalk_client_secret: SecretStr | None = None
    dingtalk_organization_id: str | None = None
    dingtalk_timeout_seconds: float = 10.0
    enforce_browser_origin: bool = True
    bootstrap_admin_username: str | None = None
    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: SecretStr | None = None
    bootstrap_admin_display_name: str = "Tenant Owner"
    bootstrap_platform_owner: bool = False
    allowed_origins: list[str] = ["http://localhost:3000", "http://localhost:5173"]
    default_tenant_id: str = "tenant-demo"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/customer_agent"
    migration_database_url: str | None = None
    database_pool_size: int = 10
    database_max_overflow: int = 5
    database_pool_timeout_seconds: float = 2.0
    database_pool_recycle_seconds: int = 900
    database_prepared_statement_cache_size: int = 100
    redis_url: str | None = None
    redis_presence_ttl_seconds: int = 360
    knowledge_sync_lease_seconds: int = 3600
    prometheus_metrics_enabled: bool = False
    prometheus_metrics_token: SecretStr | None = None
    qdrant_url: str = "http://localhost:6333"
    qdrant_trust_env: bool = False
    qdrant_startup_retry_max_seconds: float = 30.0
    qdrant_collection: str = "customer_support_knowledge_v1"
    qdrant_maintenance_timeout_seconds: int = 900
    knowledge_index_schema_version: str = "hybrid-v2"
    embedding_dimension: int = 64
    hybrid_tenant_candidate_limit: int = 40
    hybrid_global_candidate_limit: int = 30
    hybrid_rerank_candidate_limit: int = 30
    retrieval_plan_enabled: bool = True
    retrieval_query_enhancement_enabled: bool = True
    retrieval_relation_expansion_enabled: bool = True
    memory_candidate_workflow_enabled: bool = True
    tenant_experience_enabled: bool = True
    tenant_experience_release_mode: str = "shadow"
    tenant_experience_release_version: str = "experience-v1"
    tenant_experience_rollout_percent: int = 0
    tenant_experience_result_limit: int = 2
    tenant_experience_maximum_online_risk: int = 1
    tenant_experience_embedding_dimension: int = 384
    tenant_experience_minimum_similarity: float = 0.35
    tenant_experience_retrieval_timeout_seconds: float = 0.15
    tenant_experience_case_ttl_days: int = 90
    tenant_experience_outcome_observation_days: int = 7
    tenant_experience_worker_batch_size: int = 100
    tenant_experience_worker_poll_seconds: float = 60.0
    tenant_experience_worker_tenant_ids: list[str] = []
    tenant_experience_guardrail_minimum_samples: int = 30
    tenant_experience_maximum_negative_transfer_rate: float = 0.25
    tenant_experience_maximum_failure_rate_delta: float = 0.05
    hybrid_tenant_weight: float = 0.65
    hybrid_global_weight: float = 0.35
    llm_provider: str = "fake"
    embedding_provider: str = "fake"
    openai_api_key: SecretStr | None = None
    openai_base_url: str | None = None
    openai_chat_api_mode: str = "responses"
    openai_chat_model: str = "gpt-5-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    openai_timeout_seconds: float = 30.0
    openai_chat_prewarm: bool = True
    turn_planner_mode: str = "conditional"
    semantic_reviewer_mode: str = "conditional"
    deterministic_render_enabled: bool = True
    model_gateway_enabled: bool = True
    model_gateway_requests_per_minute: int = 3000
    model_gateway_tokens_per_minute: int = 3_000_000
    model_gateway_reserved_output_tokens: int = 1200
    model_circuit_failure_threshold: int = 5
    model_circuit_recovery_seconds: int = 30
    local_embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    local_embedding_cache_dir: Path = Path("./.cache/fastembed")
    local_embedding_threads: int = 4
    local_embedding_batch_size: int = 32
    local_embedding_cache_max_entries: int = 4096
    local_embedding_prewarm: bool = True
    handoff_email_enabled: bool = False
    transactional_email_enabled: bool = False
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from_address: str | None = None
    smtp_use_starttls: bool = True
    handoff_email_recipients: dict[str, list[str]] = {}
    seed_mock_business_data: bool = True
    widget_site_keys: dict[str, str | dict[str, str]] = {}
    wordpress_site_keys: dict[str, str | dict[str, str]] = {}
    public_widget_base_url: str = "http://localhost:8000"
    widget_token_secret: SecretStr = SecretStr(
        "development-only-widget-token-secret-change-before-production"
    )
    widget_token_ttl_seconds: int = 900
    widget_resume_token_ttl_seconds: int = 2_592_000
    widget_bootstrap_rate_limit_per_minute: int = 30
    widget_chat_rate_limit_per_minute: int = 20
    widget_presence_rate_limit_per_minute: int = 6
    widget_presence_source_rate_limit_per_minute: int = 120
    widget_presence_site_rate_limit_per_minute: int = 30_000
    widget_default_daily_message_limit: int = 500
    widget_asset_storage_backend: str = "filesystem"
    widget_asset_storage_path: Path = Path("./data/widget-assets")
    widget_asset_max_upload_bytes: int = 2_000_000
    widget_asset_s3_bucket: str | None = None
    widget_asset_s3_prefix: str = "widget-assets"
    widget_asset_s3_endpoint_url: str | None = None
    widget_asset_s3_region: str | None = None
    widget_asset_s3_access_key_id: str | None = None
    widget_asset_s3_secret_access_key: SecretStr | None = None
    widget_site_cache_ttl_seconds: int = 10
    widget_site_negative_cache_ttl_seconds: int = 5
    widget_site_db_fallback_concurrency: int = 10
    widget_chat_global_concurrency: int = 100
    widget_chat_site_concurrency: int = 20
    widget_chat_capacity_wait_seconds: float = 1.5
    widget_chat_capacity_lease_seconds: int = 90
    widget_chat_deadline_seconds: int = 30
    widget_sse_enabled: bool = True
    widget_sse_heartbeat_seconds: int = 15
    widget_sse_max_connection_seconds: int = 300
    site_verification_ttl_seconds: int = 3600
    auto_resolution_inactivity_hours: int = 24
    auto_resolution_execution_enabled: bool = False
    platform_global_sync_enabled: bool = False
    web_crawler_enabled: bool = False
    web_crawler_production_sync_enabled: bool = False
    web_crawler_preflight_max_sitemaps: int = 500
    web_crawler_sitemap_max_response_bytes: int = 10_000_000
    web_crawler_sitemap_max_decompressed_response_bytes: int = 20_000_000
    web_crawler_manifest_max_age_hours: int = 24
    web_crawler_max_pages: int = 5000
    web_crawler_manifest_safety_ceiling: int = 50_000
    web_crawler_full_shadow_enabled: bool = True
    web_crawler_max_sitemaps: int = 10
    web_crawler_max_response_bytes: int = 2_000_000
    web_crawler_max_decompressed_response_bytes: int = 4_000_000
    web_crawler_max_compression_ratio: float = 50.0
    web_crawler_request_timeout_seconds: float = 15.0
    web_crawler_delay_seconds: float = 0.25
    web_crawler_follow_internal_links: bool = True
    web_crawler_respect_robots_txt: bool = True
    web_crawler_batch_size: int = 250
    web_sync_worker_poll_seconds: float = 5.0
    web_sync_worker_concurrency: int = 4
    web_sync_domain_concurrency: int = 2
    web_sync_prepare_batch_size: int = 500
    web_sync_worker_lease_seconds: int = 120
    web_sync_worker_heartbeat_seconds: int = 30
    web_sync_worker_heartbeat_path: str = ""
    web_sync_staging_retention_hours: int = 48
    web_sync_max_active_tenants: int = 4
    web_sync_max_pages_in_flight: int = 8
    web_sync_max_pages_per_tenant: int = 2
    web_sync_max_finalizations: int = 1
    web_sync_work_quantum_pages: int = 20
    web_sync_work_quantum_seconds: float = 30.0
    web_sync_tenant_watchdog_seconds: float = 1020.0
    web_sync_max_no_progress_seconds: float = 90.0
    web_sync_finalization_timeout_seconds: float = 960.0
    web_sync_tenant_discovery_seconds: float = 10.0
    web_sync_idle_backoff_max_seconds: float = 30.0
    web_sync_worker_registry_ttl_seconds: int = 90
    duplicate_product_policy: str = "first_wins"
    duplicate_product_order: str = "manifest_ordinal"
    product_specification_max_age_days: int = 30
    product_price_max_age_days: int = 7
    product_stock_max_age_days: int = 7
    product_recommendation_enabled: bool = True
    product_recommendation_index_enabled: bool = False
    product_recommendation_collection: str = "product_recommendation_v1"
    product_recommendation_min_score: float = 0.15
    product_recommendation_dense_pool_size: int = 60
    product_recommendation_rerank_pool_size: int = 32
    product_recommendation_reranker_enabled: bool = False
    product_recommendation_reranker_model: str = "BAAI/bge-reranker-base"
    product_recommendation_reranker_cache_dir: Path = Path("./.cache/fastembed")
    product_recommendation_reranker_threads: int = 4
    product_recommendation_reranker_batch_size: int = 16
    product_recommendation_reranker_timeout_seconds: float = 0.8
    product_recommendation_reranker_prewarm: bool = False
    product_recommendation_llm_planner_enabled: bool = False
    product_recommendation_llm_review_enabled: bool = False
    product_recommendation_llm_model: str = "gpt-5-mini"
    product_recommendation_llm_timeout_seconds: float = 1.0
    faq_answer_cache_ttl_seconds: int = 300
    faq_answer_cache_max_entries: int = 1000
    tenant_knowledge_root: Path = Path("./examples/knowledge-sources/tenants")
    global_vault_path: Path = Path("./examples/global-vault")
    backup_status_directory: Path = Path("./backups/status")
    backup_max_age_hours: int = 36
    retention_execution_enabled: bool = False
    retention_policy_version: str = "draft-v1"
    retention_admin_session_days: int = 30
    retention_expired_memory_grace_days: int = 7
    retention_support_operation_days: int = 90
    retention_audit_event_days: int = 365

    @model_validator(mode="after")
    def validate_runtime_security(self) -> "Settings":
        if self.invite_registration_enabled and not self.email_login_enabled:
            raise ValueError("INVITE_REGISTRATION_ENABLED requires EMAIL_LOGIN_ENABLED")
        if self.self_service_signup_mode != "provision_tenant":
            raise ValueError("SELF_SERVICE_SIGNUP_MODE must be provision_tenant")
        if self.self_service_signup_enabled and not self.employee_enrollment_enabled:
            raise ValueError("SELF_SERVICE_SIGNUP_ENABLED requires EMPLOYEE_ENROLLMENT_ENABLED")
        if self.employee_enrollment_enabled and not self.email_login_enabled:
            raise ValueError("EMPLOYEE_ENROLLMENT_ENABLED requires EMAIL_LOGIN_ENABLED")
        if len(self.enrollment_token_secret.get_secret_value()) < 32:
            raise ValueError("ENROLLMENT_TOKEN_SECRET must contain at least 32 characters")
        if self.enrollment_intent_ttl_seconds < 300:
            raise ValueError("ENROLLMENT_INTENT_TTL_SECONDS must be at least 300")
        if self.enrollment_verification_ttl_seconds < 300:
            raise ValueError("ENROLLMENT_VERIFICATION_TTL_SECONDS must be at least 300")
        if self.enrollment_email_poll_seconds <= 0:
            raise ValueError("ENROLLMENT_EMAIL_POLL_SECONDS must be positive")
        if self.enrollment_email_lease_seconds < 30:
            raise ValueError("ENROLLMENT_EMAIL_LEASE_SECONDS must be at least 30")
        if self.enrollment_max_email_attempts < 1:
            raise ValueError("ENROLLMENT_MAX_EMAIL_ATTEMPTS must be positive")
        for value in self.trusted_proxy_cidrs:
            try:
                ip_network(value.strip(), strict=False)
            except ValueError as exc:
                raise ValueError("TRUSTED_PROXY_CIDRS must contain valid IP networks") from exc
        if self.site_verification_ttl_seconds < 300:
            raise ValueError("SITE_VERIFICATION_TTL_SECONDS must be at least 300")
        supported_chat_providers = {"fake", "openai"}
        supported_embedding_providers = {"fake", "fastembed", "openai"}
        if self.llm_provider not in supported_chat_providers:
            raise ValueError(f"unsupported LLM provider: {self.llm_provider}")
        if self.embedding_provider not in supported_embedding_providers:
            raise ValueError(f"unsupported embedding provider: {self.embedding_provider}")
        if "openai" in {self.llm_provider, self.embedding_provider} and self.openai_api_key is None:
            raise ValueError("OPENAI_API_KEY is required for OpenAI adapters")
        if self.admin_login_max_attempts < 2:
            raise ValueError("ADMIN_LOGIN_MAX_ATTEMPTS must be at least 2")
        if self.admin_login_window_seconds <= 0 or self.admin_login_lockout_seconds <= 0:
            raise ValueError("administrator login timing settings must be positive")
        if self.external_login_state_ttl_seconds < 60:
            raise ValueError("EXTERNAL_LOGIN_STATE_TTL_SECONDS must be at least 60")
        if self.login_completion_grant_ttl_seconds < 60:
            raise ValueError("LOGIN_COMPLETION_GRANT_TTL_SECONDS must be at least 60")
        if not 300 <= self.password_reset_ttl_seconds <= 86400:
            raise ValueError("PASSWORD_RESET_TTL_SECONDS must be between 300 and 86400")
        if self.dingtalk_timeout_seconds <= 0:
            raise ValueError("DINGTALK_TIMEOUT_SECONDS must be positive")
        configured_external_providers = {
            provider.strip().casefold()
            for provider in self.external_login_providers
            if provider.strip()
        }
        if self.dingtalk_login_enabled:
            configured_external_providers.add("dingtalk")
        unsupported_external = configured_external_providers - {"dingtalk"}
        if unsupported_external:
            raise ValueError(
                f"unsupported external login providers: {', '.join(sorted(unsupported_external))}"
            )
        self.external_login_providers = sorted(configured_external_providers)
        if "dingtalk" in configured_external_providers and (
            not self.dingtalk_client_id
            or self.dingtalk_client_secret is None
            or not self.dingtalk_organization_id
        ):
            raise ValueError(
                "DINGTALK_CLIENT_ID, DINGTALK_CLIENT_SECRET, and DINGTALK_ORGANIZATION_ID "
                "are required when DingTalk login is enabled"
            )
        if self.embedding_dimension <= 0:
            raise ValueError("EMBEDDING_DIMENSION must be positive")
        if self.redis_url is not None:
            if not self.redis_url.strip():
                self.redis_url = None
            else:
                redis_url = urlparse(self.redis_url)
                if redis_url.scheme not in {"redis", "rediss"} or not redis_url.hostname:
                    raise ValueError("REDIS_URL must be an absolute redis:// or rediss:// URL")
        if not 30 <= self.redis_presence_ttl_seconds <= 600:
            raise ValueError("REDIS_PRESENCE_TTL_SECONDS must be between 30 and 600")
        if not 100_000 <= self.widget_asset_max_upload_bytes <= 10_000_000:
            raise ValueError("WIDGET_ASSET_MAX_UPLOAD_BYTES must be between 100000 and 10000000")
        self.widget_asset_storage_backend = self.widget_asset_storage_backend.strip().casefold()
        self.widget_asset_s3_bucket = (self.widget_asset_s3_bucket or "").strip() or None
        self.widget_asset_s3_endpoint_url = (
            self.widget_asset_s3_endpoint_url or ""
        ).strip() or None
        self.widget_asset_s3_region = (self.widget_asset_s3_region or "").strip() or None
        self.widget_asset_s3_access_key_id = (
            self.widget_asset_s3_access_key_id or ""
        ).strip() or None
        if (
            self.widget_asset_s3_secret_access_key is not None
            and not self.widget_asset_s3_secret_access_key.get_secret_value().strip()
        ):
            self.widget_asset_s3_secret_access_key = None
        self.widget_asset_s3_prefix = self.widget_asset_s3_prefix.strip(" /")
        if self.widget_asset_storage_backend not in {"filesystem", "s3"}:
            raise ValueError("WIDGET_ASSET_STORAGE_BACKEND must be filesystem or s3")
        if self.widget_asset_storage_backend == "s3" and not self.widget_asset_s3_bucket:
            raise ValueError("WIDGET_ASSET_S3_BUCKET is required for S3 widget asset storage")
        if (
            ".." in self.widget_asset_s3_prefix.split("/")
            or "\\" in self.widget_asset_s3_prefix
            or len(self.widget_asset_s3_prefix) > 200
        ):
            raise ValueError("WIDGET_ASSET_S3_PREFIX must be a safe bounded object prefix")
        if bool(self.widget_asset_s3_access_key_id) != bool(self.widget_asset_s3_secret_access_key):
            raise ValueError(
                "WIDGET_ASSET_S3_ACCESS_KEY_ID and WIDGET_ASSET_S3_SECRET_ACCESS_KEY "
                "must be configured together"
            )
        if self.widget_asset_s3_endpoint_url:
            endpoint = urlparse(self.widget_asset_s3_endpoint_url)
            if endpoint.scheme not in {"http", "https"} or not endpoint.netloc:
                raise ValueError("WIDGET_ASSET_S3_ENDPOINT_URL must be an absolute HTTP URL")
        if not 300 <= self.knowledge_sync_lease_seconds <= 86400:
            raise ValueError("KNOWLEDGE_SYNC_LEASE_SECONDS must be between 300 and 86400")
        if self.prometheus_metrics_enabled and self.app_env.casefold() == "production":
            if (
                self.prometheus_metrics_token is None
                or len(self.prometheus_metrics_token.get_secret_value()) < 24
            ):
                raise ValueError(
                    "PROMETHEUS_METRICS_TOKEN must contain at least 24 characters in production"
                )
        if not self.local_embedding_model.strip():
            raise ValueError("LOCAL_EMBEDDING_MODEL is required")
        if self.local_embedding_cache_max_entries < 0:
            raise ValueError("LOCAL_EMBEDDING_CACHE_MAX_ENTRIES must not be negative")
        if not 1 <= self.local_embedding_threads <= 32:
            raise ValueError("LOCAL_EMBEDDING_THREADS must be between 1 and 32")
        if not 1 <= self.local_embedding_batch_size <= 256:
            raise ValueError("LOCAL_EMBEDDING_BATCH_SIZE must be between 1 and 256")
        if self.tenant_experience_release_mode not in {"off", "shadow", "active"}:
            raise ValueError("TENANT_EXPERIENCE_RELEASE_MODE must be off, shadow, or active")
        if not self.tenant_experience_release_version.strip():
            raise ValueError("TENANT_EXPERIENCE_RELEASE_VERSION is required")
        if not 0 <= self.tenant_experience_rollout_percent <= 100:
            raise ValueError("TENANT_EXPERIENCE_ROLLOUT_PERCENT must be between 0 and 100")
        if not 1 <= self.tenant_experience_result_limit <= 4:
            raise ValueError("TENANT_EXPERIENCE_RESULT_LIMIT must be between 1 and 4")
        if not 0 <= self.tenant_experience_maximum_online_risk <= 1:
            raise ValueError("TENANT_EXPERIENCE_MAXIMUM_ONLINE_RISK must be 0 or 1")
        if self.tenant_experience_embedding_dimension != 384:
            raise ValueError("TENANT_EXPERIENCE_EMBEDDING_DIMENSION must be 384")
        if not 0 <= self.tenant_experience_minimum_similarity <= 1:
            raise ValueError("TENANT_EXPERIENCE_MINIMUM_SIMILARITY must be between 0 and 1")
        if not 0.05 <= self.tenant_experience_retrieval_timeout_seconds <= 1:
            raise ValueError(
                "TENANT_EXPERIENCE_RETRIEVAL_TIMEOUT_SECONDS must be between 0.05 and 1"
            )
        if not 1 <= self.tenant_experience_case_ttl_days <= 365:
            raise ValueError("TENANT_EXPERIENCE_CASE_TTL_DAYS must be between 1 and 365")
        if not 1 <= self.tenant_experience_outcome_observation_days <= 90:
            raise ValueError("TENANT_EXPERIENCE_OUTCOME_OBSERVATION_DAYS must be between 1 and 90")
        if not 1 <= self.tenant_experience_worker_batch_size <= 1000:
            raise ValueError("TENANT_EXPERIENCE_WORKER_BATCH_SIZE must be between 1 and 1000")
        if not 1 <= self.tenant_experience_worker_poll_seconds <= 3600:
            raise ValueError("TENANT_EXPERIENCE_WORKER_POLL_SECONDS must be between 1 and 3600")
        if not 10 <= self.tenant_experience_guardrail_minimum_samples <= 10000:
            raise ValueError(
                "TENANT_EXPERIENCE_GUARDRAIL_MINIMUM_SAMPLES must be between 10 and 10000"
            )
        if not 0 <= self.tenant_experience_maximum_negative_transfer_rate <= 1:
            raise ValueError(
                "TENANT_EXPERIENCE_MAXIMUM_NEGATIVE_TRANSFER_RATE must be between 0 and 1"
            )
        if not 0 <= self.tenant_experience_maximum_failure_rate_delta <= 1:
            raise ValueError("TENANT_EXPERIENCE_MAXIMUM_FAILURE_RATE_DELTA must be between 0 and 1")
        self.tenant_experience_worker_tenant_ids = list(
            dict.fromkeys(
                tenant_id.strip()
                for tenant_id in self.tenant_experience_worker_tenant_ids
                if tenant_id.strip()
            )
        )
        retention_days = (
            self.retention_admin_session_days,
            self.retention_expired_memory_grace_days,
            self.retention_support_operation_days,
            self.retention_audit_event_days,
        )
        if any(value < 1 for value in retention_days):
            raise ValueError("retention durations must be at least one day")
        if not self.retention_policy_version.strip():
            raise ValueError("RETENTION_POLICY_VERSION is required")
        if self.backup_max_age_hours <= 0:
            raise ValueError("BACKUP_MAX_AGE_HOURS must be positive")
        if self.openai_chat_api_mode not in {"responses", "chat_completions"}:
            raise ValueError("OPENAI_CHAT_API_MODE must be responses or chat_completions")
        if self.turn_planner_mode not in {"always", "conditional"}:
            raise ValueError("TURN_PLANNER_MODE must be always or conditional")
        if self.semantic_reviewer_mode not in {"always", "conditional"}:
            raise ValueError("SEMANTIC_REVIEWER_MODE must be always or conditional")
        if self.openai_base_url is not None:
            if not self.openai_base_url.strip():
                self.openai_base_url = None
            else:
                parsed_base_url = urlparse(self.openai_base_url)
                if parsed_base_url.scheme not in {"http", "https"} or not parsed_base_url.netloc:
                    raise ValueError("OPENAI_BASE_URL must be an absolute HTTP or HTTPS URL")
        if self.handoff_email_enabled or self.transactional_email_enabled:
            if not self.smtp_host or not self.smtp_from_address:
                raise ValueError(
                    "SMTP_HOST and SMTP_FROM_ADDRESS are required when email delivery is enabled"
                )
        if self.employee_enrollment_enabled and not self.transactional_email_enabled:
            raise ValueError("EMPLOYEE_ENROLLMENT_ENABLED requires TRANSACTIONAL_EMAIL_ENABLED")
        if self.handoff_email_enabled:
            if not self.handoff_email_recipients:
                raise ValueError("HANDOFF_EMAIL_RECIPIENTS must map site IDs to recipients")
        if not 1 <= self.smtp_port <= 65535:
            raise ValueError("SMTP_PORT must be between 1 and 65535")
        if self.openai_timeout_seconds <= 0:
            raise ValueError("OPENAI_TIMEOUT_SECONDS must be positive")
        if self.model_gateway_requests_per_minute <= 0:
            raise ValueError("MODEL_GATEWAY_REQUESTS_PER_MINUTE must be positive")
        if self.model_gateway_tokens_per_minute <= 0:
            raise ValueError("MODEL_GATEWAY_TOKENS_PER_MINUTE must be positive")
        if not 1 <= self.model_gateway_reserved_output_tokens <= 32_000:
            raise ValueError("MODEL_GATEWAY_RESERVED_OUTPUT_TOKENS is invalid")
        if not 1 <= self.model_circuit_failure_threshold <= 100:
            raise ValueError("MODEL_CIRCUIT_FAILURE_THRESHOLD must be between 1 and 100")
        if not 5 <= self.model_circuit_recovery_seconds <= 300:
            raise ValueError("MODEL_CIRCUIT_RECOVERY_SECONDS must be between 5 and 300")
        if not 0 <= self.product_recommendation_min_score <= 1:
            raise ValueError("PRODUCT_RECOMMENDATION_MIN_SCORE must be between 0 and 1")
        if not self.product_recommendation_collection.strip():
            raise ValueError("PRODUCT_RECOMMENDATION_COLLECTION is required")
        if self.product_recommendation_dense_pool_size < 3:
            raise ValueError("PRODUCT_RECOMMENDATION_DENSE_POOL_SIZE must be at least 3")
        if self.product_recommendation_rerank_pool_size < 3:
            raise ValueError("PRODUCT_RECOMMENDATION_RERANK_POOL_SIZE must be at least 3")
        if (
            self.product_recommendation_rerank_pool_size
            > self.product_recommendation_dense_pool_size
        ):
            raise ValueError(
                "PRODUCT_RECOMMENDATION_RERANK_POOL_SIZE cannot exceed the dense pool size"
            )
        if not self.product_recommendation_reranker_model.strip():
            raise ValueError("PRODUCT_RECOMMENDATION_RERANKER_MODEL is required")
        if not 1 <= self.product_recommendation_reranker_threads <= 32:
            raise ValueError("PRODUCT_RECOMMENDATION_RERANKER_THREADS must be between 1 and 32")
        if not 1 <= self.product_recommendation_reranker_batch_size <= 128:
            raise ValueError("PRODUCT_RECOMMENDATION_RERANKER_BATCH_SIZE must be between 1 and 128")
        if self.product_recommendation_reranker_timeout_seconds <= 0:
            raise ValueError("PRODUCT_RECOMMENDATION_RERANKER_TIMEOUT_SECONDS must be positive")
        if self.product_recommendation_llm_timeout_seconds <= 0:
            raise ValueError("PRODUCT_RECOMMENDATION_LLM_TIMEOUT_SECONDS must be positive")
        if (
            self.product_recommendation_llm_planner_enabled
            or self.product_recommendation_llm_review_enabled
        ) and (self.llm_provider != "openai" or self.openai_api_key is None):
            raise ValueError(
                "OpenAI LLM configuration is required for recommendation planning or review"
            )
        if not 60 <= self.qdrant_maintenance_timeout_seconds <= 3600:
            raise ValueError("QDRANT_MAINTENANCE_TIMEOUT_SECONDS must be between 60 and 3600")
        if not 1 <= self.qdrant_startup_retry_max_seconds <= 300:
            raise ValueError("QDRANT_STARTUP_RETRY_MAX_SECONDS must be between 1 and 300")
        if not 60 <= self.widget_token_ttl_seconds <= 3600:
            raise ValueError("WIDGET_TOKEN_TTL_SECONDS must be between 60 and 3600")
        if not 86_400 <= self.widget_resume_token_ttl_seconds <= 31_536_000:
            raise ValueError("WIDGET_RESUME_TOKEN_TTL_SECONDS must be between 1 day and 1 year")
        if not 1 <= self.database_pool_size <= 100:
            raise ValueError("DATABASE_POOL_SIZE must be between 1 and 100")
        if not 0 <= self.database_max_overflow <= 100:
            raise ValueError("DATABASE_MAX_OVERFLOW must be between 0 and 100")
        if not 0.1 <= self.database_pool_timeout_seconds <= 30:
            raise ValueError("DATABASE_POOL_TIMEOUT_SECONDS must be between 0.1 and 30")
        if not 30 <= self.database_pool_recycle_seconds <= 86_400:
            raise ValueError("DATABASE_POOL_RECYCLE_SECONDS must be between 30 and 86400")
        if not 0 <= self.database_prepared_statement_cache_size <= 10_000:
            raise ValueError("DATABASE_PREPARED_STATEMENT_CACHE_SIZE must be between 0 and 10000")
        widget_limits = (
            self.widget_bootstrap_rate_limit_per_minute,
            self.widget_chat_rate_limit_per_minute,
            self.widget_presence_rate_limit_per_minute,
            self.widget_presence_source_rate_limit_per_minute,
            self.widget_presence_site_rate_limit_per_minute,
            self.widget_default_daily_message_limit,
        )
        if any(value <= 0 for value in widget_limits):
            raise ValueError("public widget limits must be positive")
        if not 1 <= self.widget_site_cache_ttl_seconds <= 60:
            raise ValueError("WIDGET_SITE_CACHE_TTL_SECONDS must be between 1 and 60")
        if not 1 <= self.widget_site_negative_cache_ttl_seconds <= 10:
            raise ValueError("WIDGET_SITE_NEGATIVE_CACHE_TTL_SECONDS must be between 1 and 10")
        if not 1 <= self.widget_site_db_fallback_concurrency <= 100:
            raise ValueError("WIDGET_SITE_DB_FALLBACK_CONCURRENCY must be between 1 and 100")
        if not 1 <= self.widget_chat_global_concurrency <= 10_000:
            raise ValueError("WIDGET_CHAT_GLOBAL_CONCURRENCY must be between 1 and 10000")
        if not 1 <= self.widget_chat_site_concurrency <= self.widget_chat_global_concurrency:
            raise ValueError("WIDGET_CHAT_SITE_CONCURRENCY must not exceed the global limit")
        if not 0.1 <= self.widget_chat_capacity_wait_seconds <= 5:
            raise ValueError("WIDGET_CHAT_CAPACITY_WAIT_SECONDS must be between 0.1 and 5")
        if not 30 <= self.widget_chat_capacity_lease_seconds <= 300:
            raise ValueError("WIDGET_CHAT_CAPACITY_LEASE_SECONDS must be between 30 and 300")
        if not 5 <= self.widget_chat_deadline_seconds <= 60:
            raise ValueError("WIDGET_CHAT_DEADLINE_SECONDS must be between 5 and 60")
        if self.widget_chat_capacity_lease_seconds <= self.widget_chat_deadline_seconds:
            raise ValueError("widget chat capacity lease must exceed the chat deadline")
        if not 5 <= self.widget_sse_heartbeat_seconds <= 60:
            raise ValueError("WIDGET_SSE_HEARTBEAT_SECONDS must be between 5 and 60")
        if not 60 <= self.widget_sse_max_connection_seconds <= 3600:
            raise ValueError("WIDGET_SSE_MAX_CONNECTION_SECONDS must be between 60 and 3600")
        if not 1 <= self.auto_resolution_inactivity_hours <= 720:
            raise ValueError("AUTO_RESOLUTION_INACTIVITY_HOURS must be between 1 and 720")
        public_widget_url = urlparse(self.public_widget_base_url)
        if public_widget_url.scheme not in {"http", "https"} or not public_widget_url.netloc:
            raise ValueError("PUBLIC_WIDGET_BASE_URL must be an absolute HTTP or HTTPS URL")
        admin_public_url = urlparse(self.admin_public_base_url)
        if admin_public_url.scheme not in {"http", "https"} or not admin_public_url.netloc:
            raise ValueError("ADMIN_PUBLIC_BASE_URL must be an absolute HTTP or HTTPS URL")
        if not 1 <= self.web_crawler_max_pages <= 10_000:
            raise ValueError("WEB_CRAWLER_MAX_PAGES must be between 1 and 10000")
        if not 1_000 <= self.web_crawler_manifest_safety_ceiling <= 1_000_000:
            raise ValueError("WEB_CRAWLER_MANIFEST_SAFETY_CEILING must be between 1000 and 1000000")
        if not 1 <= self.web_crawler_max_sitemaps <= 100:
            raise ValueError("WEB_CRAWLER_MAX_SITEMAPS must be between 1 and 100")
        if not 1 <= self.web_crawler_preflight_max_sitemaps <= 500:
            raise ValueError("WEB_CRAWLER_PREFLIGHT_MAX_SITEMAPS must be between 1 and 500")
        if not 1_000 <= self.web_crawler_sitemap_max_response_bytes <= 20_000_000:
            raise ValueError("WEB_CRAWLER_SITEMAP_MAX_RESPONSE_BYTES is outside the safe range")
        if not (
            self.web_crawler_sitemap_max_response_bytes
            <= self.web_crawler_sitemap_max_decompressed_response_bytes
            <= 50_000_000
        ):
            raise ValueError(
                "WEB_CRAWLER_SITEMAP_MAX_DECOMPRESSED_RESPONSE_BYTES is outside the safe range"
            )
        if not 1 <= self.web_crawler_manifest_max_age_hours <= 720:
            raise ValueError("WEB_CRAWLER_MANIFEST_MAX_AGE_HOURS must be between 1 and 720")
        if not 1_000 <= self.web_crawler_max_response_bytes <= 20_000_000:
            raise ValueError("WEB_CRAWLER_MAX_RESPONSE_BYTES is outside the safe range")
        if not (
            self.web_crawler_max_response_bytes
            <= self.web_crawler_max_decompressed_response_bytes
            <= 20_000_000
        ):
            raise ValueError(
                "WEB_CRAWLER_MAX_DECOMPRESSED_RESPONSE_BYTES is outside the safe range"
            )
        if not 1 <= self.web_crawler_max_compression_ratio <= 100:
            raise ValueError("WEB_CRAWLER_MAX_COMPRESSION_RATIO must be between 1 and 100")
        if self.web_crawler_request_timeout_seconds <= 0:
            raise ValueError("WEB_CRAWLER_REQUEST_TIMEOUT_SECONDS must be positive")
        if not 0 <= self.web_crawler_delay_seconds <= 60:
            raise ValueError("WEB_CRAWLER_DELAY_SECONDS must be between 0 and 60")
        if not 1 <= self.web_crawler_batch_size <= 1000:
            raise ValueError("WEB_CRAWLER_BATCH_SIZE must be between 1 and 1000")
        if not 0.5 <= self.web_sync_worker_poll_seconds <= 300:
            raise ValueError("WEB_SYNC_WORKER_POLL_SECONDS must be between 0.5 and 300")
        if not 1 <= self.web_sync_worker_concurrency <= 16:
            raise ValueError("WEB_SYNC_WORKER_CONCURRENCY must be between 1 and 16")
        if not 1 <= self.web_sync_domain_concurrency <= self.web_sync_worker_concurrency:
            raise ValueError("WEB_SYNC_DOMAIN_CONCURRENCY must be between 1 and worker concurrency")
        if not 1 <= self.web_sync_prepare_batch_size <= 5000:
            raise ValueError("WEB_SYNC_PREPARE_BATCH_SIZE must be between 1 and 5000")
        if not 30 <= self.web_sync_worker_lease_seconds <= 3600:
            raise ValueError("WEB_SYNC_WORKER_LEASE_SECONDS must be between 30 and 3600")
        if not 5 <= self.web_sync_worker_heartbeat_seconds < self.web_sync_worker_lease_seconds:
            raise ValueError("WEB_SYNC_WORKER_HEARTBEAT_SECONDS must be below the lease duration")
        if not 1 <= self.web_sync_staging_retention_hours <= 168:
            raise ValueError("WEB_SYNC_STAGING_RETENTION_HOURS must be between 1 and 168")
        if not 1 <= self.web_sync_max_active_tenants <= 32:
            raise ValueError("WEB_SYNC_MAX_ACTIVE_TENANTS must be between 1 and 32")
        if not 1 <= self.web_sync_max_pages_in_flight <= 64:
            raise ValueError("WEB_SYNC_MAX_PAGES_IN_FLIGHT must be between 1 and 64")
        if not 1 <= self.web_sync_max_pages_per_tenant <= 16:
            raise ValueError("WEB_SYNC_MAX_PAGES_PER_TENANT must be between 1 and 16")
        if self.web_sync_max_pages_per_tenant > self.web_sync_max_pages_in_flight:
            raise ValueError("WEB_SYNC_MAX_PAGES_PER_TENANT cannot exceed the global page limit")
        if not 1 <= self.web_sync_max_finalizations <= self.web_sync_max_active_tenants:
            raise ValueError("WEB_SYNC_MAX_FINALIZATIONS must be between 1 and active tenant limit")
        if not 1 <= self.web_sync_work_quantum_pages <= 10_000:
            raise ValueError("WEB_SYNC_WORK_QUANTUM_PAGES must be between 1 and 10000")
        if not 1 <= self.web_sync_work_quantum_seconds <= 3600:
            raise ValueError("WEB_SYNC_WORK_QUANTUM_SECONDS must be between 1 and 3600")
        if not (self.web_sync_work_quantum_seconds < self.web_sync_tenant_watchdog_seconds <= 7200):
            raise ValueError(
                "WEB_SYNC_TENANT_WATCHDOG_SECONDS must exceed the work quantum "
                "and not exceed 7200 seconds"
            )
        if not (
            self.web_sync_worker_heartbeat_seconds
            < self.web_sync_max_no_progress_seconds
            < self.web_sync_tenant_watchdog_seconds
        ):
            raise ValueError(
                "WEB_SYNC_MAX_NO_PROGRESS_SECONDS must exceed the heartbeat interval "
                "and not exceed the tenant watchdog"
            )
        if not (
            self.qdrant_maintenance_timeout_seconds
            <= self.web_sync_finalization_timeout_seconds
            < self.web_sync_tenant_watchdog_seconds
        ):
            raise ValueError(
                "WEB_SYNC_FINALIZATION_TIMEOUT_SECONDS must cover the Qdrant maintenance "
                "timeout and remain below the tenant watchdog"
            )
        if not 0.5 <= self.web_sync_tenant_discovery_seconds <= 300:
            raise ValueError("WEB_SYNC_TENANT_DISCOVERY_SECONDS must be between 0.5 and 300")
        if not (
            self.web_sync_worker_poll_seconds <= self.web_sync_idle_backoff_max_seconds <= 3600
        ):
            raise ValueError(
                "WEB_SYNC_IDLE_BACKOFF_MAX_SECONDS must be between poll interval and 3600"
            )
        if not (
            self.web_sync_worker_heartbeat_seconds * 2
            <= self.web_sync_worker_registry_ttl_seconds
            <= 3600
        ):
            raise ValueError(
                "WEB_SYNC_WORKER_REGISTRY_TTL_SECONDS must be at least two heartbeats "
                "and at most 3600"
            )
        if self.duplicate_product_policy not in {"first_wins", "block", "manual_review"}:
            raise ValueError("DUPLICATE_PRODUCT_POLICY must be first_wins, block, or manual_review")
        if self.duplicate_product_order != "manifest_ordinal":
            raise ValueError("DUPLICATE_PRODUCT_ORDER must be manifest_ordinal")
        if (
            min(
                self.product_specification_max_age_days,
                self.product_price_max_age_days,
                self.product_stock_max_age_days,
            )
            < 1
        ):
            raise ValueError("product freshness windows must be positive")
        if not 0 <= self.faq_answer_cache_ttl_seconds <= 3600:
            raise ValueError("FAQ_ANSWER_CACHE_TTL_SECONDS must be between 0 and 3600")
        if not 1 <= self.faq_answer_cache_max_entries <= 10000:
            raise ValueError("FAQ_ANSWER_CACHE_MAX_ENTRIES must be between 1 and 10000")
        overlapping_site_keys = set(self.widget_site_keys) & set(self.wordpress_site_keys)
        if overlapping_site_keys:
            raise ValueError("WIDGET_SITE_KEYS and WORDPRESS_SITE_KEYS must not overlap")

        if self.app_env.casefold() == "production":
            if self.redis_url is None:
                raise ValueError("REDIS_URL is required in production")
            if not self.prometheus_metrics_enabled:
                raise ValueError("PROMETHEUS_METRICS_ENABLED must be true in production")
            if self.auth_mode == "mock":
                raise ValueError("mock authentication is prohibited in production")
            if self.seed_mock_business_data:
                raise ValueError("mock business data seeding is prohibited in production")
            if self.llm_provider == "fake" or self.embedding_provider == "fake":
                raise ValueError("fake model providers are prohibited in production")
            if not self.admin_cookie_secure:
                raise ValueError("secure administrator cookies are required in production")
            if self.widget_token_secret.get_secret_value().startswith("development-only-"):
                raise ValueError("WIDGET_TOKEN_SECRET must be replaced in production")
            if (
                self.employee_enrollment_enabled
                and self.enrollment_token_secret.get_secret_value().startswith("development-only-")
            ):
                raise ValueError("ENROLLMENT_TOKEN_SECRET must be replaced in production")
            if public_widget_url.scheme != "https":
                raise ValueError("PUBLIC_WIDGET_BASE_URL must use HTTPS in production")
            if admin_public_url.scheme != "https":
                raise ValueError("ADMIN_PUBLIC_BASE_URL must use HTTPS in production")
            if not self.enforce_browser_origin:
                raise ValueError("browser origin enforcement is required in production")
            if not str(self.backup_status_directory).replace("\\", "/").startswith("/"):
                raise ValueError("production backup status directory must be absolute")
            if (
                self.retention_execution_enabled
                and self.retention_policy_version.casefold().startswith("draft")
            ):
                raise ValueError("draft retention policy cannot execute in production")
            if not self.allowed_origins:
                raise ValueError("at least one explicit production origin is required")
            for origin in self.allowed_origins:
                parsed = urlparse(origin)
                if parsed.scheme != "https" or not parsed.netloc or parsed.hostname == "localhost":
                    raise ValueError("production origins must be explicit HTTPS origins")
                if "*" in origin:
                    raise ValueError("wildcard production origins are prohibited")
        return self

    @property
    def effective_widget_site_keys(self) -> dict[str, str | dict[str, str]]:
        return {**self.wordpress_site_keys, **self.widget_site_keys}


@lru_cache
def get_settings() -> Settings:
    return Settings()
