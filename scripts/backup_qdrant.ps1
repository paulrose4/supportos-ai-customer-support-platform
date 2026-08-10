param(
    [string]$EnvFile = ".env.production",
    [string]$BackupDirectory = "./backups/qdrant",
    [string]$StatusDirectory = "./backups/status"
)

$ErrorActionPreference = "Stop"
$resolvedEnv = (Resolve-Path -LiteralPath $EnvFile).Path
New-Item -ItemType Directory -Path $BackupDirectory -Force | Out-Null
$resolvedBackup = (Resolve-Path -LiteralPath $BackupDirectory).Path
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$fileName = "qdrant-$stamp.tar.gz"
$outputPath = Join-Path $resolvedBackup $fileName
$env:ENV_FILE = $resolvedEnv
$compose = @("compose", "--env-file", $resolvedEnv, "-f", "compose.production.yaml")
$containerId = (& docker @compose "ps" "-q" "qdrant").Trim()
if (-not $containerId) { throw "Qdrant container is not running." }

try {
    & docker @compose "stop" "qdrant"
    & docker run --rm --volumes-from $containerId -v "${resolvedBackup}:/backup" alpine:3.22 tar -czf "/backup/$fileName" -C /qdrant/storage .
    if ($LASTEXITCODE -ne 0) { throw "Qdrant archive failed." }
} finally {
    & docker @compose "start" "qdrant"
}
if ((Get-Item -LiteralPath $outputPath).Length -eq 0) { throw "Backup is empty." }
New-Item -ItemType Directory -Path $StatusDirectory -Force | Out-Null
$resolvedStatus = (Resolve-Path -LiteralPath $StatusDirectory).Path
$artifact = Get-Item -LiteralPath $outputPath
$manifest = [ordered]@{
    artifact_type = "qdrant"
    completed_at = (Get-Date).ToUniversalTime().ToString("o")
    file_name = $artifact.Name
    size_bytes = $artifact.Length
    sha256 = (Get-FileHash -LiteralPath $outputPath -Algorithm SHA256).Hash.ToLowerInvariant()
    restore_verified_at = $null
}
$tempStatus = Join-Path $resolvedStatus "qdrant.json.tmp"
$finalStatus = Join-Path $resolvedStatus "qdrant.json"
$manifest | ConvertTo-Json | Set-Content -LiteralPath $tempStatus -Encoding utf8
Move-Item -LiteralPath $tempStatus -Destination $finalStatus -Force
Write-Output $outputPath
