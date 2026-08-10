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
$containerId = (& docker @compose "ps" "-q" "qdrant").Trim()
if (-not $containerId) { throw "Qdrant container is not running." }

try {
    & docker @compose "stop" "qdrant"
    & docker run --rm --volumes-from $containerId -v "${resolvedBackup}:/backup.tar.gz:ro" alpine:3.22 sh -c "find /qdrant/storage -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + && tar -xzf /backup.tar.gz -C /qdrant/storage"
    if ($LASTEXITCODE -ne 0) { throw "Qdrant restore failed." }
} finally {
    & docker @compose "start" "qdrant"
}
Write-Output "Qdrant restore completed from $resolvedBackup"
