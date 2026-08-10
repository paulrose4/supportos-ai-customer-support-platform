param(
    [string]$EnvFile = ".env.production"
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $EnvFile)) {
    throw "Missing $EnvFile. Copy .env.production.example and replace all placeholders first."
}

python -m scripts.validate_production_env --env-file $EnvFile
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$env:ENV_FILE = $EnvFile
$crawlerEnabled = Select-String `
    -LiteralPath $EnvFile `
    -Pattern '^\s*WEB_CRAWLER_ENABLED\s*=\s*true\s*$' `
    -CaseSensitive:$false `
    -Quiet
$profileArguments = if ($crawlerEnabled) { @("--profile", "web-sync") } else { @() }

docker compose --env-file $EnvFile -f compose.production.yaml @profileArguments config --quiet
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
docker compose --env-file $EnvFile -f compose.production.yaml @profileArguments build --pull
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if ($crawlerEnabled) {
    docker compose --env-file $EnvFile -f compose.production.yaml @profileArguments `
        run --rm --no-deps --user 0:0 --cap-add CHOWN web-sync-worker sh -c `
        'owner="$(id -u app):$(id -g app)"; chown "$owner" /app/web-sync-status'
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
if (-not $crawlerEnabled) {
    docker compose --env-file $EnvFile -f compose.production.yaml `
        --profile web-sync rm --stop --force web-sync-worker *> $null
}
docker compose --env-file $EnvFile -f compose.production.yaml @profileArguments up -d --remove-orphans
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
docker compose --env-file $EnvFile -f compose.production.yaml @profileArguments ps
if ($crawlerEnabled) {
    Write-Host "Website synchronization worker profile: enabled"
} else {
    Write-Host "Website synchronization worker profile: disabled"
}
