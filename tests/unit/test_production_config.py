from pathlib import Path

from scripts.validate_production_env import validate_production_environment


def _write_valid_environment(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "APP_DOMAIN=support.acme.test",
                "ACME_EMAIL=ops@acme.test",
                "APP_ENV=production",
                "AUTH_MODE=session",
                "ADMIN_COOKIE_SECURE=true",
                'ALLOWED_ORIGINS=["https://support.acme.test"]',
                "PUBLIC_WIDGET_BASE_URL=https://support.acme.test",
                "ADMIN_PUBLIC_BASE_URL=https://support.acme.test",
                "SEED_MOCK_BUSINESS_DATA=false",
                "LLM_PROVIDER=openai",
                "EMBEDDING_PROVIDER=openai",
                "OPENAI_API_KEY=test-api-key-value",
                "WIDGET_TOKEN_SECRET=test-production-widget-token-secret-1234567890",
                "POSTGRES_DB=customer_agent",
                "POSTGRES_USER=customer_agent",
                "POSTGRES_PASSWORD=abcdefghijklmnopqrstuvwxyz123456",
                "POSTGRES_MIGRATOR_PASSWORD=migrator-abcdefghijklmnopqrstuvwxyz",
                "POSTGRES_APP_PASSWORD=application-abcdefghijklmnopqrstuvwxyz",
                "POSTGRES_BACKUP_PASSWORD=backup-abcdefghijklmnopqrstuvwxyz",
                "DATABASE_URL=postgresql+asyncpg://app_tenant:application-abcdefghijklmnopqrstuvwxyz@postgres:5432/customer_agent",
                "MIGRATION_DATABASE_URL=postgresql+asyncpg://migrator:migrator-abcdefghijklmnopqrstuvwxyz@postgres:5432/customer_agent",
                "REDIS_PASSWORD=redis-abcdefghijklmnopqrstuvwxyz",
                "REDIS_URL=redis://:redis-abcdefghijklmnopqrstuvwxyz@redis:6379/0",
                "PROMETHEUS_METRICS_ENABLED=true",
                "PROMETHEUS_METRICS_TOKEN=prometheus-abcdefghijklmnopqrstuvwxyz",
                "QDRANT_URL=http://qdrant:6333",
                "BACKUP_STATUS_DIRECTORY=/app/backup-status",
                "BACKUP_MAX_AGE_HOURS=36",
                "RETENTION_POLICY_VERSION=privacy-v1",
            ]
        ),
        encoding="utf-8",
    )


def test_valid_production_environment_passes(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.production"
    _write_valid_environment(env_file)

    assert validate_production_environment(env_file) == []


def test_example_placeholders_are_rejected(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.production"
    _write_valid_environment(env_file)
    content = env_file.read_text(encoding="utf-8").replace(
        "support.acme.test", "support.example.com"
    )
    env_file.write_text(content, encoding="utf-8")

    errors = validate_production_environment(env_file)

    assert "APP_DOMAIN still contains an example placeholder" in errors
