param(
    [switch]$Apply,
    [string]$Database = "customer_agent"
)

$ErrorActionPreference = "Stop"

if ($Database -ne "customer_agent") {
    throw "This cleanup is intentionally limited to the local customer_agent database."
}

$targetPredicate = @"
tenant_id LIKE 'test-%'
OR tenant_id LIKE 'tenant-identity-%'
OR tenant_id LIKE 'tenant-security-%'
OR tenant_id LIKE 'tenant-team-%'
OR tenant_id LIKE 'tenant-governance-%'
"@

$previewSql = @"
SELECT count(*) AS test_tenants
FROM tenants
WHERE $targetPredicate;

SELECT count(DISTINCT u.user_id) AS removable_test_users
FROM users u
JOIN tenant_memberships m ON m.user_id = u.user_id
JOIN tenants t ON t.tenant_id = m.tenant_id
WHERE ($($targetPredicate.Replace('tenant_id', 't.tenant_id')))
  AND NOT EXISTS (SELECT 1 FROM email_identities e WHERE e.user_id = u.user_id);

SELECT tenant_id, name, created_at
FROM tenants
WHERE $targetPredicate
ORDER BY tenant_id;
"@

Write-Host "Local test-data cleanup preview for database '$Database'"
docker compose exec -T postgres psql -U postgres -d $Database -v ON_ERROR_STOP=1 -P pager=off -c $previewSql
if ($LASTEXITCODE -ne 0) {
    throw "Could not inspect local test data."
}

if (-not $Apply) {
    Write-Host "Dry run only. Re-run with -Apply after creating a database backup."
    exit 0
}

$cleanupSql = @"
BEGIN;

CREATE TEMP TABLE cleanup_target_tenants ON COMMIT DROP AS
SELECT tenant_id
FROM tenants
WHERE $targetPredicate;

CREATE TEMP TABLE cleanup_target_users ON COMMIT DROP AS
SELECT DISTINCT m.user_id
FROM tenant_memberships m
JOIN cleanup_target_tenants t ON t.tenant_id = m.tenant_id;

DO `$cleanup`$
DECLARE
    target_count integer;
    preserved_count integer;
    table_record record;
BEGIN
    SELECT count(*) INTO target_count FROM cleanup_target_tenants;
    SELECT count(*) INTO preserved_count FROM tenants WHERE tenant_id = 'tenant-demo';
    IF target_count = 0 THEN
        RAISE EXCEPTION 'No matching test tenants were found; refusing an unexpected cleanup run.';
    END IF;
    IF preserved_count <> 1 THEN
        RAISE EXCEPTION 'tenant-demo is missing; refusing cleanup.';
    END IF;

    -- Remove the few restrictive parent/child graphs before the generic tenant sweep.
    DELETE FROM web_sync_jobs
    WHERE tenant_id IN (SELECT tenant_id FROM cleanup_target_tenants);
    DELETE FROM knowledge_document_versions
    WHERE tenant_id IN (SELECT tenant_id FROM cleanup_target_tenants);

    FOR table_record IN
        SELECT DISTINCT table_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND column_name = 'tenant_id'
          AND table_name <> 'tenants'
        ORDER BY table_name
    LOOP
        EXECUTE format(
            'DELETE FROM %I WHERE tenant_id IN (SELECT tenant_id FROM cleanup_target_tenants)',
            table_record.table_name
        );
    END LOOP;

    DELETE FROM tenants
    WHERE tenant_id IN (SELECT tenant_id FROM cleanup_target_tenants);

    DELETE FROM users u
    WHERE u.user_id IN (SELECT user_id FROM cleanup_target_users)
      AND NOT EXISTS (SELECT 1 FROM tenant_memberships m WHERE m.user_id = u.user_id)
      AND NOT EXISTS (SELECT 1 FROM email_identities e WHERE e.user_id = u.user_id);
END
`$cleanup`$;

COMMIT;

SELECT count(*) AS remaining_tenants FROM tenants;
SELECT count(*) AS remaining_users FROM users;
SELECT tenant_id, name, status FROM tenants ORDER BY tenant_id;
SELECT u.user_id, u.display_name, e.display_email
FROM users u
LEFT JOIN email_identities e ON e.user_id = u.user_id
ORDER BY u.display_name, u.user_id;
"@

docker compose exec -T postgres psql -U postgres -d $Database -v ON_ERROR_STOP=1 -P pager=off -c $cleanupSql
if ($LASTEXITCODE -ne 0) {
    throw "Cleanup failed and the transaction was rolled back."
}

Write-Host "Local test data cleanup completed."
