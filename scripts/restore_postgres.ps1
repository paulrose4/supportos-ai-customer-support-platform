param(
    [Parameter(Mandatory = $true)]
    [string]$BackupFile,
    [string]$EnvFile = ".env.production",
    [switch]$ConfirmRestore
)

$ErrorActionPreference = "Stop"
if (-not $ConfirmRestore) { throw "Restore is destructive. Re-run with -ConfirmRestore." }
$resolvedEnv = (Resolve-Path -LiteralPath $EnvFile).Path
$resolvedBackup = (Resolve-Path -LiteralPath $BackupFile).Path
$env:ENV_FILE = $resolvedEnv
$compose = @("compose", "--env-file", $resolvedEnv, "-f", "compose.production.yaml")
$containerId = (& docker @compose "ps" "-q" "postgres").Trim()
if (-not $containerId) { throw "PostgreSQL container is not running." }
$dbUser = ((Get-Content -LiteralPath $resolvedEnv) -match '^POSTGRES_USER=') -replace '^POSTGRES_USER=', ''
$dbName = ((Get-Content -LiteralPath $resolvedEnv) -match '^POSTGRES_DB=') -replace '^POSTGRES_DB=', ''
$containerPath = "/tmp/postgres-restore.dump"

& docker cp $resolvedBackup "${containerId}:$containerPath"
if ($LASTEXITCODE -ne 0) { throw "docker cp failed." }
try {
    & docker @compose "stop" "api"
    & docker @compose "exec" "-T" "postgres" "dropdb" "--username=$dbUser" "--if-exists" "--force" $dbName
    if ($LASTEXITCODE -ne 0) { throw "dropdb failed." }
    & docker @compose "exec" "-T" "postgres" "pg_restore" "--username=$dbUser" "--dbname=postgres" "--create" "--no-owner" "--no-acl" $containerPath
    if ($LASTEXITCODE -ne 0) { throw "pg_restore failed." }
    & docker @compose "run" "--rm" "migrate"
    if ($LASTEXITCODE -ne 0) { throw "Alembic migration failed after restore." }
    & docker @compose "up" "-d" "api"
} finally {
    & docker @compose "exec" "-T" "postgres" "rm" "-f" $containerPath 2>$null
}
Write-Output "PostgreSQL restore completed from $resolvedBackup"

