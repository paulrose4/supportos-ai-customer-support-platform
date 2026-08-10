#!/usr/bin/env sh
set -eu

ENV_FILE="${ENV_FILE:-.env.production}"
command -v docker >/dev/null 2>&1 || { echo "docker is required" >&2; exit 1; }
[ -f "$ENV_FILE" ] || { echo "missing $ENV_FILE; copy .env.production.example first" >&2; exit 1; }
export ENV_FILE

# Compose mounts this file as a Docker secret so Prometheus can scrape the API
# without exposing the metrics credential through Caddy or a public port.
PROMETHEUS_RUNTIME_DIR="deploy/runtime"
PROMETHEUS_TOKEN_FILE="$PROMETHEUS_RUNTIME_DIR/prometheus_metrics_token"
mkdir -p "$PROMETHEUS_RUNTIME_DIR"
prometheus_token="$(awk -F= '$1 == "PROMETHEUS_METRICS_TOKEN" { print substr($0, index($0, "=") + 1); exit }' "$ENV_FILE")"
[ -n "$prometheus_token" ] || {
  echo "PROMETHEUS_METRICS_TOKEN is required to prepare the Prometheus secret" >&2
  exit 1
}
case "$prometheus_token" in
  *[!A-Za-z0-9._-]*)
    echo "PROMETHEUS_METRICS_TOKEN must contain only URL-safe characters" >&2
    exit 1
    ;;
esac
umask 077
printf '%s' "$prometheus_token" > "$PROMETHEUS_TOKEN_FILE"
chown 65534:65534 "$PROMETHEUS_TOKEN_FILE"
chmod 0400 "$PROMETHEUS_TOKEN_FILE"
unset prometheus_token

WEB_SYNC_PROFILE_ENABLED=false
if grep -Eiq '^[[:space:]]*WEB_CRAWLER_ENABLED[[:space:]]*=[[:space:]]*true[[:space:]]*$' "$ENV_FILE"; then
  WEB_SYNC_PROFILE_ENABLED=true
fi

compose() {
  if [ "$WEB_SYNC_PROFILE_ENABLED" = "true" ]; then
    docker compose --env-file "$ENV_FILE" -f compose.production.yaml --profile web-sync "$@"
  else
    docker compose --env-file "$ENV_FILE" -f compose.production.yaml "$@"
  fi
}

compose config --quiet
compose build --pull
compose run --rm --no-deps migrate python -m scripts.validate_production_env --from-environment
if [ "$WEB_SYNC_PROFILE_ENABLED" = "true" ]; then
  compose run --rm --no-deps --user 0:0 --cap-add CHOWN web-sync-worker sh -c \
    'owner="$(id -u app):$(id -g app)"; chown "$owner" /app/web-sync-status'
fi
if [ "$WEB_SYNC_PROFILE_ENABLED" = "false" ]; then
  docker compose --env-file "$ENV_FILE" -f compose.production.yaml \
    --profile web-sync rm --stop --force web-sync-worker >/dev/null 2>&1 || true
fi
compose up -d --remove-orphans
compose ps

if [ "$WEB_SYNC_PROFILE_ENABLED" = "true" ]; then
  echo "Website synchronization worker profile: enabled"
else
  echo "Website synchronization worker profile: disabled"
fi
echo "Deployment started. Verify HTTPS and readiness at https://$(sed -n 's/^APP_DOMAIN=//p' "$ENV_FILE")/health/ready"
