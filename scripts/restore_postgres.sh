#!/usr/bin/env sh
set -eu

if [ "${1:-}" != "--confirm" ] || [ -z "${2:-}" ]; then
  echo "usage: $0 --confirm BACKUP_FILE" >&2
  exit 2
fi

BACKUP_FILE="$2"
ENV_FILE="${ENV_FILE:-.env.production}"
[ -s "$BACKUP_FILE" ] || { echo "backup file is missing or empty: $BACKUP_FILE" >&2; exit 1; }
[ -f "$ENV_FILE" ] || { echo "missing $ENV_FILE" >&2; exit 1; }
export ENV_FILE
compose() { docker compose --env-file "$ENV_FILE" -f compose.production.yaml "$@"; }
DB_USER="$(sed -n 's/^POSTGRES_USER=//p' "$ENV_FILE")"
DB_NAME="$(sed -n 's/^POSTGRES_DB=//p' "$ENV_FILE")"

compose stop api
compose exec -T postgres dropdb --username="$DB_USER" --if-exists --force "$DB_NAME"
cat "$BACKUP_FILE" | compose exec -T postgres pg_restore \
  --username="$DB_USER" --dbname=postgres --create --no-owner --no-acl
compose run --rm migrate
compose up -d api

echo "PostgreSQL restore completed from $BACKUP_FILE"

