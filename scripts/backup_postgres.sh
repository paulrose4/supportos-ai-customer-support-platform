#!/usr/bin/env sh
set -eu

ENV_FILE="${ENV_FILE:-.env.production}"
BACKUP_DIR="${BACKUP_DIR:-./backups/postgres}"
STATUS_DIR="${STATUS_DIR:-./backups/status}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUTPUT="$BACKUP_DIR/postgres-$STAMP.dump"

[ -f "$ENV_FILE" ] || { echo "missing $ENV_FILE" >&2; exit 1; }
mkdir -p "$BACKUP_DIR"
export ENV_FILE
compose() { docker compose --env-file "$ENV_FILE" -f compose.production.yaml "$@"; }

compose exec -T postgres \
  pg_dump --username="backup_reader" \
  --dbname="$(sed -n 's/^POSTGRES_DB=//p' "$ENV_FILE")" \
  --format=custom --create --no-owner --no-acl > "$OUTPUT"

test -s "$OUTPUT"
mkdir -p "$STATUS_DIR"
SIZE_BYTES="$(stat -c %s "$OUTPUT")"
SHA256="$(sha256sum "$OUTPUT" | awk '{print $1}')"
COMPLETED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
TEMP_STATUS="$STATUS_DIR/postgres.json.tmp"
printf '{"artifact_type":"postgres","completed_at":"%s","file_name":"%s","size_bytes":%s,"sha256":"%s","restore_verified_at":null}\n' \
  "$COMPLETED_AT" "$(basename "$OUTPUT")" "$SIZE_BYTES" "$SHA256" > "$TEMP_STATUS"
mv "$TEMP_STATUS" "$STATUS_DIR/postgres.json"
echo "$OUTPUT"
