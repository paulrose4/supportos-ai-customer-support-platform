# Database Roles and RLS

## Roles

Production uses four database identities:

| Role | Purpose | Key restriction |
| --- | --- | --- |
| `POSTGRES_USER` | Initial database administration and restore | Never used by API or routine backup |
| `migrator` | Owns schema objects and runs Alembic | Not available to the API process |
| `app_tenant` | Runs API queries | Non-owner, `NOBYPASSRLS` |
| `backup_reader` | Creates complete read-only dumps | Read-only; `BYPASSRLS` and operationally restricted |

The API container explicitly overrides `MIGRATION_DATABASE_URL` with an empty value. Alembic prefers `MIGRATION_DATABASE_URL`, while application repositories use `DATABASE_URL`.

## Fresh database

On an empty PostgreSQL volume, `scripts/postgres/init-production-roles.sh` runs before migrations. It creates the roles, revokes public schema creation, installs current and default grants, and marks the API role `NOBYPASSRLS`.

Required URLs:

```text
DATABASE_URL=postgresql+asyncpg://app_tenant:...@postgres:5432/customer_agent
MIGRATION_DATABASE_URL=postgresql+asyncpg://migrator:...@postgres:5432/customer_agent
```

Use distinct high-entropy passwords for the administrator, migrator, application, and backup roles.

## Existing database volume

Docker initialization scripts do not rerun on an existing volume. Before changing `DATABASE_URL`, execute the role script once inside the PostgreSQL container with the required password environment present, then run Alembic as `migrator`. Verify grants before restarting the API.

Do not solve a permission error by granting table ownership, superuser, or `BYPASSRLS` to `app_tenant`.

## Tenant transaction context

Every tenant business transaction sets a transaction-local value:

```sql
SELECT set_config('app.tenant_id', '<trusted-tenant-id>', true);
```

RLS policies use that value in both `USING` and `WITH CHECK`. The setting is local to the transaction and therefore cannot leak through the connection pool. Global approved knowledge additionally requires the service-controlled `app.global_access=on` context.

Identity and public Widget registry tables are control-plane tables and are accessed through narrowly scoped application services. Tenant tables remain protected even when a repository accidentally omits a tenant predicate.

`platform_site_directory` and `platform_tenant_entitlements` are sanitized,
cross-tenant read projections. `app_tenant` has `SELECT` access so the guarded
platform administration service can query them, but has no direct
`INSERT`/`UPDATE`/`DELETE` access. Database triggers owned by `migrator` are the
only projection writers.

`platform_site_directory.verification_expires_at` is retained only to derive an
effective `expired` state for pending verification challenges. The platform API
returns the derived state but does not expose the expiry timestamp or any
verification token material.

## Acceptance queries

Connect as `app_tenant` and verify:

```sql
SELECT rolname, rolsuper, rolbypassrls
FROM pg_roles
WHERE rolname IN ('migrator', 'app_tenant', 'backup_reader');
```

`app_tenant` must show `false` for both `rolsuper` and `rolbypassrls`. Run the integration suite with both restricted and migration URLs; `tests/integration/test_postgres_rls.py` proves that raw SQL cannot read or write another tenant.

The backup role is intentionally allowed to bypass RLS for complete backups, but has no write grants. Restrict its password to the backup job and rotate it independently.
