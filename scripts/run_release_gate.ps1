param(
    [switch]$RunInfrastructure,
    [string]$TenantId = $env:RELEASE_TENANT_ID,
    [int]$Days = 30
)

$ErrorActionPreference = "Stop"

Write-Host "[1/5] Static and architecture gates"
python -m ruff check .
python -m ruff format --check .
python scripts/check_architecture.py

Write-Host "[2/5] Unit and contract tests"
python -m pytest

Write-Host "[3/5] Dashboard build"
npm ci --prefix dashboard
npm run build --prefix dashboard

if ($RunInfrastructure) {
    Write-Host "[4/5] PostgreSQL/Qdrant/Redis integration tests"
    powershell -ExecutionPolicy Bypass -File scripts/run_integration_tests.ps1
} else {
    Write-Host "[4/5] Infrastructure gates skipped (pass -RunInfrastructure in staging)"
}

if ([string]::IsNullOrWhiteSpace($TenantId)) {
    Write-Host "[5/5] Operational quality report skipped: RELEASE_TENANT_ID is not set"
    exit 0
}

Write-Host "[5/5] Operational quality report"
python scripts/release_quality_report.py --tenant-id $TenantId --days $Days
exit $LASTEXITCODE
