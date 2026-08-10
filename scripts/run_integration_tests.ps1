param(
    [string]$TestDatabase = "customer_agent_integration",
    [int]$QdrantReadyTimeoutSeconds = 600
)

$ErrorActionPreference = "Stop"

if ($TestDatabase -notmatch "^[A-Za-z0-9_]+$") {
    throw "TestDatabase may contain only letters, numbers, and underscores."
}

$protectedDatabases = @("customer_agent", "postgres", "template0", "template1")
if ($TestDatabase -in $protectedDatabases -or $TestDatabase -notmatch "(_test|_integration)$") {
    throw "Refusing to recreate '$TestDatabase'. Integration databases must end in _test or _integration and cannot be a development/system database."
}

$appRole = "codex_integration_app"
$appPassword = "codex_integration_app"
$migratorRole = "codex_integration_migrator"
$migratorPassword = "codex_integration_migrator"
$postgresUrl = "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/$TestDatabase"
$appUrl = "postgresql+asyncpg://${appRole}:${appPassword}@127.0.0.1:5432/$TestDatabase"
$migratorUrl = "postgresql+asyncpg://${migratorRole}:${migratorPassword}@127.0.0.1:5432/$TestDatabase"
$env:NO_PROXY = "127.0.0.1,localhost"
$env:no_proxy = "127.0.0.1,localhost"

docker compose up -d postgres qdrant redis
if ($LASTEXITCODE -ne 0) {
    throw "PostgreSQL, Qdrant, or Redis did not start successfully."
}

$postgresReady = $false
for ($attempt = 1; $attempt -le $QdrantReadyTimeoutSeconds; $attempt++) {
    docker compose exec -T postgres pg_isready -U postgres -d postgres *> $null
    if ($LASTEXITCODE -eq 0) {
        $postgresReady = $true
        break
    }
    Start-Sleep -Seconds 1
}
if (-not $postgresReady) {
    throw "PostgreSQL did not become ready within $QdrantReadyTimeoutSeconds seconds."
}

$qdrantReady = $false
for ($attempt = 1; $attempt -le $QdrantReadyTimeoutSeconds; $attempt++) {
    curl.exe --noproxy "*" --silent --fail http://127.0.0.1:6333/collections *> $null
    if ($LASTEXITCODE -eq 0) {
        $qdrantReady = $true
        break
    }
    Start-Sleep -Seconds 1
}
if (-not $qdrantReady) {
    throw "Qdrant did not become ready within $QdrantReadyTimeoutSeconds seconds."
}

$redisReady = $false
for ($attempt = 1; $attempt -le $QdrantReadyTimeoutSeconds; $attempt++) {
    docker compose exec -T redis redis-cli ping *> $null
    if ($LASTEXITCODE -eq 0) {
        $redisReady = $true
        break
    }
    Start-Sleep -Seconds 1
}
if (-not $redisReady) {
    throw "Redis did not become ready within $QdrantReadyTimeoutSeconds seconds."
}

Write-Host "Recreating isolated database: $TestDatabase"

docker compose exec -T postgres psql -U postgres -d postgres -v ON_ERROR_STOP=1 `
    -c "DROP DATABASE IF EXISTS $TestDatabase WITH (FORCE);" `
    -c "CREATE DATABASE $TestDatabase;"
if ($LASTEXITCODE -ne 0) {
    throw "Could not recreate the isolated integration database."
}

docker compose exec -T postgres psql -U postgres -d $TestDatabase -v ON_ERROR_STOP=1 `
    -c "CREATE EXTENSION IF NOT EXISTS vector;"
if ($LASTEXITCODE -ne 0) {
    throw "Could not install the vector extension in the isolated integration database."
}

Write-Host "Configuring migration role"
$migratorExists = docker compose exec -T postgres psql -U postgres -d postgres -tAc `
    "SELECT 1 FROM pg_roles WHERE rolname = '$migratorRole';"
if ($LASTEXITCODE -ne 0) {
    throw "Could not inspect the migration role."
}
if ([string]::IsNullOrWhiteSpace($migratorExists) -or $migratorExists.Trim() -ne "1") {
    docker compose exec -T postgres psql -U postgres -d postgres -v ON_ERROR_STOP=1 `
        -c "CREATE ROLE $migratorRole LOGIN PASSWORD '$migratorPassword' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOINHERIT;"
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create the migration role."
    }
}
docker compose exec -T postgres psql -U postgres -d $TestDatabase -v ON_ERROR_STOP=1 `
    -c "ALTER ROLE $migratorRole WITH LOGIN PASSWORD '$migratorPassword' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOINHERIT;" `
    -c "GRANT CONNECT ON DATABASE $TestDatabase TO $migratorRole;" `
    -c "GRANT USAGE, CREATE ON SCHEMA public TO $migratorRole;"
if ($LASTEXITCODE -ne 0) {
    throw "Could not configure the migration role."
}

$env:DATABASE_URL = $postgresUrl
$env:TEST_DATABASE_URL = $postgresUrl
Write-Host "Applying Alembic migrations"
$env:MIGRATION_DATABASE_URL = $migratorUrl
python -m alembic upgrade head
if ($LASTEXITCODE -ne 0) {
    throw "Alembic migration failed for the integration database."
}

Write-Host "Configuring restricted RLS test role"
$roleExists = docker compose exec -T postgres psql -U postgres -d postgres -tAc `
    "SELECT 1 FROM pg_roles WHERE rolname = '$appRole';"
if ($LASTEXITCODE -ne 0) {
    throw "Could not inspect the integration-test role."
}
if ([string]::IsNullOrWhiteSpace($roleExists) -or $roleExists.Trim() -ne "1") {
    docker compose exec -T postgres psql -U postgres -d postgres -v ON_ERROR_STOP=1 `
        -c "CREATE ROLE $appRole LOGIN PASSWORD '$appPassword' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOINHERIT;"
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create the restricted integration-test role."
    }
}

docker compose exec -T postgres psql -U postgres -d $TestDatabase -v ON_ERROR_STOP=1 `
    -c "ALTER ROLE $appRole WITH LOGIN PASSWORD '$appPassword' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOINHERIT;" `
    -c "GRANT CONNECT ON DATABASE $TestDatabase TO $appRole;" `
    -c "GRANT USAGE ON SCHEMA public TO $appRole;" `
    -c "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO $appRole;" `
    -c "GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO $appRole;" `
    -c "REVOKE INSERT, UPDATE, DELETE ON TABLE platform_site_directory, platform_tenant_entitlements FROM $appRole;" `
    -c "REVOKE ALL ON SEQUENCE platform_site_directory_id_seq FROM $appRole;"
if ($LASTEXITCODE -ne 0) {
    throw "Could not configure the restricted integration-test role."
}

$env:RUN_INTEGRATION_TESTS = "1"
$env:RLS_DATABASE_URL = $appUrl
$env:TEST_QDRANT_URL = "http://127.0.0.1:6333"
$env:TEST_REDIS_URL = "redis://127.0.0.1:6379/15"
Write-Host "Running PostgreSQL, Qdrant, and RLS integration tests"
python -m pytest tests/integration
exit $LASTEXITCODE
