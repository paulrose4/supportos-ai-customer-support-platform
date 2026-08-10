from typing import Any

import pytest
from pydantic import ValidationError

from app.config import Settings


def _production_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "app_env": "production",
        "auth_mode": "disabled",
        "seed_mock_business_data": False,
        "admin_cookie_secure": True,
        "admin_public_base_url": "https://support.example.com",
        "public_widget_base_url": "https://widget.example.com",
        "allowed_origins": ["https://support.example.com"],
        "llm_provider": "openai",
        "embedding_provider": "openai",
        "openai_api_key": "test-placeholder-key",
        "widget_token_secret": "test-production-widget-token-secret-1234567890",
        "redis_url": "redis://:test-password@redis:6379/0",
        "prometheus_metrics_enabled": True,
        "prometheus_metrics_token": "test-prometheus-token-1234567890",
        "backup_status_directory": "/app/backup-status",
    }
    values.update(overrides)
    return Settings(**values)


def test_mock_auth_is_rejected_in_production() -> None:
    with pytest.raises(ValidationError):
        _production_settings(auth_mode="mock")


def test_mock_business_seed_is_rejected_in_production() -> None:
    with pytest.raises(ValidationError):
        _production_settings(seed_mock_business_data=True)


def test_production_site_key_only_mode_is_allowed() -> None:
    settings = _production_settings()

    assert settings.auth_mode == "disabled"


def test_production_settings_helper_ignores_local_dotenv(tmp_path, monkeypatch) -> None:
    (tmp_path / ".env").write_text(
        "PUBLIC_WIDGET_BASE_URL=http://insecure.example.test\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    settings = _production_settings()

    assert settings.public_widget_base_url == "https://widget.example.com"


def test_invalid_redis_url_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, redis_url="http://localhost:6379")


def test_widget_asset_storage_configuration_is_validated() -> None:
    settings = Settings(
        _env_file=None,
        widget_asset_storage_backend=" S3 ",
        widget_asset_s3_bucket="private-widget-assets",
        widget_asset_s3_endpoint_url="https://account.r2.cloudflarestorage.com",
    )
    assert settings.widget_asset_storage_backend == "s3"

    with pytest.raises(ValidationError, match="WIDGET_ASSET_S3_BUCKET"):
        Settings(_env_file=None, widget_asset_storage_backend="s3")
    with pytest.raises(ValidationError, match="must be configured together"):
        Settings(_env_file=None, widget_asset_s3_access_key_id="access-only")
    with pytest.raises(ValidationError, match="WIDGET_ASSET_S3_ENDPOINT_URL"):
        Settings(
            _env_file=None,
            widget_asset_storage_backend="s3",
            widget_asset_s3_bucket="private-widget-assets",
            widget_asset_s3_endpoint_url="file:///tmp/assets",
        )


def test_invalid_trusted_proxy_cidr_is_rejected() -> None:
    with pytest.raises(ValidationError, match="TRUSTED_PROXY_CIDRS"):
        Settings(_env_file=None, trusted_proxy_cidrs=["not-a-network"])


def test_fake_models_are_rejected_in_production() -> None:
    with pytest.raises(ValidationError):
        _production_settings(llm_provider="fake")


def test_insecure_admin_cookie_is_rejected_in_production() -> None:
    with pytest.raises(ValidationError):
        _production_settings(admin_cookie_secure=False)


def test_localhost_origin_is_rejected_in_production() -> None:
    with pytest.raises(ValidationError):
        _production_settings(allowed_origins=["http://localhost:8090"])


def test_openai_provider_requires_api_key() -> None:
    with pytest.raises(ValidationError):
        Settings(llm_provider="openai", openai_api_key=None)


def test_empty_optional_openai_base_url_is_treated_as_unset() -> None:
    settings = Settings(_env_file=None, openai_base_url="")

    assert settings.openai_base_url is None


def test_latency_model_gate_modes_are_validated() -> None:
    settings = Settings(
        _env_file=None,
        turn_planner_mode="conditional",
        semantic_reviewer_mode="always",
    )

    assert settings.turn_planner_mode == "conditional"
    assert settings.semantic_reviewer_mode == "always"
    with pytest.raises(ValidationError, match="TURN_PLANNER_MODE"):
        Settings(_env_file=None, turn_planner_mode="off")
    with pytest.raises(ValidationError, match="SEMANTIC_REVIEWER_MODE"):
        Settings(_env_file=None, semantic_reviewer_mode="shadow")


def test_qdrant_maintenance_timeout_rejects_online_query_timeout_values() -> None:
    with pytest.raises(ValidationError, match="QDRANT_MAINTENANCE_TIMEOUT_SECONDS"):
        Settings(qdrant_maintenance_timeout_seconds=30)


def test_qdrant_startup_retry_bound_must_be_positive() -> None:
    with pytest.raises(ValidationError, match="QDRANT_STARTUP_RETRY_MAX_SECONDS"):
        Settings(qdrant_startup_retry_max_seconds=0)


def test_invalid_login_throttle_configuration_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(admin_login_max_attempts=1)
    with pytest.raises(ValidationError):
        Settings(admin_login_window_seconds=0)


def test_invitation_registration_requires_email_login() -> None:
    with pytest.raises(ValidationError, match="requires EMAIL_LOGIN_ENABLED"):
        Settings(
            _env_file=None,
            email_login_enabled=False,
            invite_registration_enabled=True,
        )


def test_draft_retention_execution_is_rejected_in_production() -> None:
    with pytest.raises(ValidationError):
        _production_settings(retention_execution_enabled=True, retention_policy_version="draft-v1")


def test_generic_widget_site_keys_include_legacy_fallback() -> None:
    settings = Settings(
        _env_file=None,
        widget_site_keys={"new-key": {"tenant_id": "tenant-a", "site_id": "site-a"}},
        wordpress_site_keys={"legacy-key": "tenant-legacy"},
    )

    assert settings.effective_widget_site_keys == {
        "legacy-key": "tenant-legacy",
        "new-key": {"tenant_id": "tenant-a", "site_id": "site-a"},
    }


def test_generic_and_legacy_widget_keys_cannot_overlap() -> None:
    with pytest.raises(ValidationError, match="must not overlap"):
        Settings(
            _env_file=None,
            widget_site_keys={"same-key": "tenant-a"},
            wordpress_site_keys={"same-key": "tenant-a"},
        )


def test_invalid_web_crawler_limits_are_rejected() -> None:
    invalid_values = (
        {"web_crawler_max_pages": 0},
        {"web_crawler_max_sitemaps": 0},
        {"web_crawler_max_response_bytes": 999},
        {"web_crawler_request_timeout_seconds": 0},
        {"web_crawler_delay_seconds": -0.1},
    )
    for values in invalid_values:
        with pytest.raises(ValidationError):
            Settings(_env_file=None, **values)


def test_auto_resolution_window_is_bounded() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, auto_resolution_inactivity_hours=0)


def test_fastembed_provider_is_supported() -> None:
    settings = Settings(
        _env_file=None,
        embedding_provider="fastembed",
        embedding_dimension=384,
    )

    assert settings.embedding_provider == "fastembed"
    assert settings.local_embedding_model.endswith("paraphrase-multilingual-MiniLM-L12-v2")


def test_invalid_local_embedding_runtime_settings_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, local_embedding_model="")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, local_embedding_threads=0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, local_embedding_batch_size=0)


def test_tenant_experience_worker_configuration_is_bounded_and_normalized() -> None:
    settings = Settings(
        _env_file=None,
        tenant_experience_worker_poll_seconds=30,
        tenant_experience_worker_tenant_ids=[" tenant-a ", "tenant-a", "tenant-b"],
    )

    assert settings.tenant_experience_worker_tenant_ids == ["tenant-a", "tenant-b"]

    with pytest.raises(ValidationError, match="TENANT_EXPERIENCE_WORKER_POLL_SECONDS"):
        Settings(_env_file=None, tenant_experience_worker_poll_seconds=0)
