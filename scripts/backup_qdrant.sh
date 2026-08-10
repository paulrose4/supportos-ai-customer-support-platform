#!/usr/bin/env sh
set -eu

ENV_FILE="${ENV_FILE:-.env.production}"
BACKUP_DIR="${BACKUP_DIR:-./backups/qdrant}"
STATUS_DIR="${STATUS_DIR:-./backups/status}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUTPUT_NAME="qdrant-$STAMP.tar.gz"
ABS_BACKUP_DIR="$(mkdir -p "$BACKUP_DIR" && cd "$BACKUP_DIR" && pwd)"
[ -f "$ENV_FILE" ] || { echo "missing $ENV_FILE" >&2; exit 1; }
export ENV_FILE
compose() { docker compose --env-file "$ENV_FILE" -f compose.production.yaml "$@"; }
CONTAINER_ID="$(compose ps -q qdrant)"
[ -n "$CONTAINER_ID" ] || { echo "qdrant container is not running" >&2; exit 1; }

restart_qdrant() { compose start qdrant >/dev/null; }
trap restart_qdrant EXIT INT TERM
compose stop qdrant >/dev/null
docker run --rm --volumes-from "$CONTAINER_ID" -v "$ABS_BACKUP_DIR:/backup" alpine:3.22 \
  tar -czf "/backup/$OUTPUT_NAME" -C /qdrant/storage .
restart_qdrant
trap - EXIT INT TERM

OUTPUT="$ABS_BACKUP_DIR/$OUTPUT_NAME"
test -s "$OUTPUT"
mkdir -p "$STATUS_DIR"
SIZE_BYTES="$(stat -c %s "$OUTPUT")"
SHA256="$(sha256sum "$OUTPUT" | awk '{print $1}')"
COMPLETED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
TEMP_STATUS="$STATUS_DIR/qdrant.json.tmp"
printf '{"artifact_type":"qdrant","completed_at":"%s","file_name":"%s","size_bytes":%s,"sha256":"%s","restore_verified_at":null}\n' \
  "$COMPLETED_AT" "$OUTPUT_NAME" "$SIZE_BYTES" "$SHA256" > "$TEMP_STATUS"
mv "$TEMP_STATUS" "$STATUS_DIR/qdrant.json"
echo "$OUTPUT"
