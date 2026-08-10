#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
from urllib.parse import urlparse

from app.config import Settings

_PLACEHOLDER_MARKERS = ("replace-with", "example.com", "placeholder")


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _validate_values(values: dict[str, str], *, env_file: Path | None) -> list[str]:
    errors: list[str] = []
    required = {
        "APP_DOMAIN",
        "ACME_EMAIL",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_MIGRATOR_PASSWORD",
        "POSTGRES_APP_PASSWORD",
        "POSTGRES_BACKUP_PASSWORD",
        "DATABASE_URL",
        "MIGRATION_DATABASE_URL",
        "REDIS_PASSWORD",
        "REDIS_URL",
        "QDRANT_URL",
        "PUBLIC_WIDGET_BASE_URL",
        "WIDGET_TOKEN_SECRET",
        "BACKUP_STATUS_DIRECTORY",
        "BACKUP_MAX_AGE_HOURS",
        "RETENTION_POLICY_VERSION",
    }
    for key in sorted(required):
        if not values.get(key):
            errors.append(f"{key} is required")

    for key in (
        "APP_DOMAIN",
        "ACME_EMAIL",
        "POSTGRES_PASSWORD",
        "POSTGRES_MIGRATOR_PASSWORD",
        "POSTGRES_APP_PASSWORD",
        "POSTGRES_BACKUP_PASSWORD",
        "REDIS_PASSWORD",
        "WIDGET_TOKEN_SECRET",
        "PROMETHEUS_METRICS_TOKEN",
        "OPENAI_API_KEY",
    ):
        value = values.get(key, "").casefold()
        if any(marker in value for marker in _PLACEHOLDER_MARKERS):
            errors.append(f"{key} still contains an example placeholder")

    if len(values.get("POSTGRES_PASSWORD", "")) < 24:
        errors.append("POSTGRES_PASSWORD must contain at least 24 characters")
    if len(values.get("POSTGRES_MIGRATOR_PASSWORD", "")) < 24:
        errors.append("POSTGRES_MIGRATOR_PASSWORD must contain at least 24 characters")
    if len(values.get("POSTGRES_APP_PASSWORD", "")) < 24:
        errors.append("POSTGRES_APP_PASSWORD must contain at least 24 characters")
    if len(values.get("POSTGRES_BACKUP_PASSWORD", "")) < 24:
        errors.append("POSTGRES_BACKUP_PASSWORD must contain at least 24 characters")
    if len(values.get("REDIS_PASSWORD", "")) < 24:
        errors.append("REDIS_PASSWORD must contain at least 24 characters")
    if len(values.get("WIDGET_TOKEN_SECRET", "")) < 24:
        errors.append("WIDGET_TOKEN_SECRET must contain at least 24 characters")

    bootstrap_password = values.get("BOOTSTRAP_ADMIN_PASSWORD")
    if bootstrap_password and len(bootstrap_password) < 16:
        errors.append("BOOTSTRAP_ADMIN_PASSWORD must contain at least 16 characters")

    app_domain = values.get("APP_DOMAIN", "")
    if app_domain.startswith(("http://", "https://")) or "/" in app_domain:
        errors.append("APP_DOMAIN must be a hostname without scheme or path")

    database_url = urlparse(values.get("DATABASE_URL", ""))
    if database_url.hostname != "postgres":
        errors.append("DATABASE_URL must use the private Docker hostname 'postgres'")
    migration_database_url = urlparse(values.get("MIGRATION_DATABASE_URL", ""))
    if migration_database_url.hostname != "postgres":
        errors.append("MIGRATION_DATABASE_URL must use the private Docker hostname 'postgres'")
    if database_url.username != "app_tenant":
        errors.append("DATABASE_URL must use the restricted 'app_tenant' role")
    if migration_database_url.username != "migrator":
        errors.append("MIGRATION_DATABASE_URL must use the dedicated 'migrator' role")
    if database_url.username == migration_database_url.username:
        errors.append("application and migration database roles must be different")
    qdrant_url = urlparse(values.get("QDRANT_URL", ""))
    if qdrant_url.hostname != "qdrant":
        errors.append("QDRANT_URL must use the private Docker hostname 'qdrant'")
    redis_url = urlparse(values.get("REDIS_URL", ""))
    if redis_url.hostname != "redis" or redis_url.scheme not in {"redis", "rediss"}:
        errors.append("REDIS_URL must use the private Docker hostname 'redis'")
    if not redis_url.password:
        errors.append("REDIS_URL must include authentication")
    public_widget_url = urlparse(values.get("PUBLIC_WIDGET_BASE_URL", ""))
    if public_widget_url.scheme != "https" or public_widget_url.hostname != app_domain:
        errors.append("PUBLIC_WIDGET_BASE_URL must use the production HTTPS hostname")
    if (
        values.get("PROMETHEUS_METRICS_ENABLED", "false").casefold() == "true"
        and len(values.get("PROMETHEUS_METRICS_TOKEN", "")) < 24
    ):
        errors.append("PROMETHEUS_METRICS_TOKEN must contain at least 24 characters")
    if values.get("TRANSACTIONAL_EMAIL_ENABLED", "false").casefold() == "true" and (
        not values.get("SMTP_HOST") or not values.get("SMTP_FROM_ADDRESS")
    ):
        errors.append("enabled transactional email requires SMTP_HOST and SMTP_FROM_ADDRESS")

    try:
        settings = Settings(_env_file=env_file)
    except Exception as exception:  # noqa: BLE001
        errors.append(f"application settings are invalid: {exception}")
    else:
        expected_origin = f"https://{app_domain}"
        if settings.allowed_origins != [expected_origin]:
            errors.append(f"ALLOWED_ORIGINS must be exactly ['{expected_origin}']")
    return errors


def validate_production_environment(path: Path) -> list[str]:
    if not path.is_file():
        return [f"environment file does not exist: {path}"]
    return _validate_values(_read_env(path), env_file=path)


def validate_current_environment() -> list[str]:
    return _validate_values(dict(os.environ), env_file=None)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate production environment configuration")
    parser.add_argument("--env-file", default=".env.production", type=Path)
    parser.add_argument("--from-environment", action="store_true")
    arguments = parser.parse_args()
    errors = (
        validate_current_environment()
        if arguments.from_environment
        else validate_production_environment(arguments.env_file)
    )
    if errors:
        print("Production configuration is not ready:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Production configuration is valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
