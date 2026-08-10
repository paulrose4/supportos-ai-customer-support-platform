#!/usr/bin/env sh
set -eu

MODE="${1:-}"
ENV_FILE="${ENV_FILE:-.env.production}"
COMPOSE_FILE="${COMPOSE_FILE:-compose.production.yaml}"
FAULT_DURATION_SECONDS="${FAULT_DURATION_SECONDS:-30}"

if [ "${CONFIRM_STAGING:-0}" != "1" ]; then
  echo "CONFIRM_STAGING=1 is required; fault drills must never target production." >&2
  exit 2
fi
if [ ! -f "$ENV_FILE" ] || [ ! -f "$COMPOSE_FILE" ]; then
  echo "The configured environment or Compose file does not exist." >&2
  exit 2
fi

compose() {
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
}

case "$MODE" in
  redis-pause)
    cleanup() {
      compose unpause redis >/dev/null 2>&1 || true
    }
    trap cleanup EXIT INT TERM
    compose pause redis
    sleep "$FAULT_DURATION_SECONDS"
    compose unpause redis
    trap - EXIT INT TERM
    ;;
  api-restart)
    compose restart api
    ;;
  *)
    echo "Usage: CONFIRM_STAGING=1 $0 redis-pause|api-restart" >&2
    exit 2
    ;;
esac
