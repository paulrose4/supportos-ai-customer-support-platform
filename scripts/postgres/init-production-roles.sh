#!/usr/bin/env sh
set -eu

: "${POSTGRES_MIGRATOR_PASSWORD:?POSTGRES_MIGRATOR_PASSWORD is required}"
: "${POSTGRES_APP_PASSWORD:?POSTGRES_APP_PASSWORD is required}"
: "${POSTGRES_BACKUP_PASSWORD:?POSTGRES_BACKUP_PASSWORD is required}"

psql \
  --set=ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=migrator_password="$POSTGRES_MIGRATOR_PASSWORD" \
  --set=app_password="$POSTGRES_APP_PASSWORD" \
  --set=backup_password="$POSTGRES_BACKUP_PASSWORD" <<'SQL'
-- Extension installation is a schema-owner operation. Install it during
-- database bootstrap so the restricted migrator can apply later migrations.
CREATE EXTENSION IF NOT EXISTS vector;

SELECT format(
    'CREATE ROLE migrator LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT',
    :'migrator_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'migrator') \gexec

SELECT format(
    'CREATE ROLE app_tenant LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOINHERIT',
    :'app_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_tenant') \gexec

SELECT format(
    'CREATE ROLE backup_reader LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE BYPASSRLS NOINHERIT',
    :'backup_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backup_reader') \gexec

ALTER ROLE app_tenant NOBYPASSRLS;
ALTER ROLE backup_reader BYPASSRLS;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

SELECT format('GRANT CONNECT, TEMPORARY ON DATABASE %I TO migrator', current_database()) \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO app_tenant', current_database()) \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO backup_reader', current_database()) \gexec
GRANT USAGE, CREATE ON SCHEMA public TO migrator;
GRANT USAGE ON SCHEMA public TO app_tenant, backup_reader;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_tenant;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO app_tenant;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO backup_reader;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO backup_reader;

ALTER DEFAULT PRIVILEGES FOR ROLE migrator IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_tenant;
ALTER DEFAULT PRIVILEGES FOR ROLE migrator IN SCHEMA public
  GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO app_tenant;
ALTER DEFAULT PRIVILEGES FOR ROLE migrator IN SCHEMA public
  GRANT SELECT ON TABLES TO backup_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE migrator IN SCHEMA public
  GRANT SELECT ON SEQUENCES TO backup_reader;

-- These projections are cross-tenant read models. Source-table triggers owned
-- by the migrator are their only writers.
SELECT 'REVOKE INSERT, UPDATE, DELETE ON TABLE public.platform_site_directory, public.platform_tenant_entitlements FROM app_tenant'
WHERE to_regclass('public.platform_site_directory') IS NOT NULL
  AND to_regclass('public.platform_tenant_entitlements') IS NOT NULL \gexec
SELECT 'REVOKE ALL ON SEQUENCE public.platform_site_directory_id_seq FROM app_tenant'
WHERE to_regclass('public.platform_site_directory_id_seq') IS NOT NULL \gexec
SQL
