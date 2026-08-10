param(
    [string]$EnvFile = ".env.production",
    [string]$BackupDirectory = "./backups/postgres",
    [string]$StatusDirectory = "./backups/status"
)

$ErrorActionPreference = "Stop"
$resolvedEnv = (Resolve-Path -LiteralPath $EnvFile).Path
New-Item -ItemType Directory -Path $BackupDirectory -Force | Out-Null
$resolvedBackup = (Resolve-Path -LiteralPath $BackupDirectory).Path
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$fileName = "postgres-$stamp.dump"
$outputPath = Join-Path $resolvedBackup $fileName
$env:ENV_FILE = $resolvedEnv
$compose = @("compose", "--env-file", $resolvedEnv, "-f", "compose.production.yaml")
$containerId = (& docker @compose "ps" "-q" "postgres").Trim()
if (-not $containerId) { throw "PostgreSQL container is not running." }

$dbUser = "backup_reader"
$dbName = ((Get-Content -LiteralPath $resolvedEnv) -match '^POSTGRES_DB=') -replace '^POSTGRES_DB=', ''
$containerPath = "/tmp/$fileName"
try {
    & docker @compose "exec" "-T" "postgres" "pg_dump" "--username=$dbUser" "--dbname=$dbName" "--format=custom" "--create" "--no-owner" "--no-acl" "--file=$containerPath"
    if ($LASTEXITCODE -ne 0) { throw "pg_dump failed." }
    & docker cp "${containerId}:$containerPath" $outputPath
    if ($LASTEXITCODE -ne 0) { throw "docker cp failed." }
} finally {
    & docker @compose "exec" "-T" "postgres" "rm" "-f" $containerPath 2>$null
}
if ((Get-Item -LiteralPath $outputPath).Length -eq 0) { throw "Backup is empty." }
New-Item -ItemType Directory -Path $StatusDirectory -Force | Out-Null
$resolvedStatus = (Resolve-Path -LiteralPath $StatusDirectory).Path
$artifact = Get-Item -LiteralPath $outputPath
$manifest = [ordered]@{
    artifact_type = "postgres"
    completed_at = (Get-Date).ToUniversalTime().ToString("o")
    file_name = $artifact.Name
    size_bytes = $artifact.Length
    sha256 = (Get-FileHash -LiteralPath $outputPath -Algorithm SHA256).Hash.ToLowerInvariant()
    restore_verified_at = $null
}
$tempStatus = Join-Path $resolvedStatus "postgres.json.tmp"
$finalStatus = Join-Path $resolvedStatus "postgres.json"
$manifest | ConvertTo-Json | Set-Content -LiteralPath $tempStatus -Encoding utf8
Move-Item -LiteralPath $tempStatus -Destination $finalStatus -Force
Write-Output $outputPath
