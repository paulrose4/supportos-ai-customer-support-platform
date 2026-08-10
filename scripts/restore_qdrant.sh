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
ABS_BACKUP_FILE="$(cd "$(dirname "$BACKUP_FILE")" && pwd)/$(basename "$BACKUP_FILE")"
export ENV_FILE
compose() { docker compose --env-file "$ENV_FILE" -f compose.production.yaml "$@"; }
CONTAINER_ID="$(compose ps -q qdrant)"
[ -n "$CONTAINER_ID" ] || { echo "qdrant container is not running" >&2; exit 1; }

restart_qdrant() { compose start qdrant >/dev/null; }
trap restart_qdrant EXIT INT TERM
compose stop qdrant >/dev/null
docker run --rm --volumes-from "$CONTAINER_ID" -v "$ABS_BACKUP_FILE:/backup.tar.gz:ro" alpine:3.22 \
  sh -c 'find /qdrant/storage -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + && tar -xzf /backup.tar.gz -C /qdrant/storage'
restart_qdrant
trap - EXIT INT TERM

echo "Qdrant restore completed from $BACKUP_FILE"
