"""add sanitized cross-workspace platform projections"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "e4f5a6b7c8d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SUPPORT_SITE_TENANT_FK = "fk_support_sites_tenant"
_MIGRATION_READ_POLICY = "platform_site_directory_migration_read_all"
_SUPPORT_SITE_TENANT_FK_OWNER = "alembic:a1b2c3d4e5f6"


def _run_autocommit(statement: str) -> None:
    with op.get_context().autocommit_block():
        op.execute(statement)


def _support_site_tenant_constraint_exists() -> bool:
    # Alembic's offline renderer intentionally has no live bind.  The
    # generated migration is applied once against the target database, so it
    # must emit the same add/validate sequence without trying to inspect the
    # PostgreSQL catalog while rendering SQL.
    if op.get_context().as_sql:
        return False
    row = (
        op.get_bind()
        .execute(
            sa.text(
                """
            SELECT
                constraints.contype = 'f'
                AND constraints.confrelid = 'public.tenants'::regclass
                AND constraints.conkey = ARRAY(
                    SELECT attributes.attnum
                    FROM pg_catalog.pg_attribute AS attributes
                    WHERE attributes.attrelid = 'public.support_sites'::regclass
                      AND attributes.attname = 'tenant_id'
                      AND NOT attributes.attisdropped
                )
                AND constraints.confkey = ARRAY(
                    SELECT attributes.attnum
                    FROM pg_catalog.pg_attribute AS attributes
                    WHERE attributes.attrelid = 'public.tenants'::regclass
                      AND attributes.attname = 'tenant_id'
                      AND NOT attributes.attisdropped
                )
                AND constraints.confdeltype = 'c' AS definition_matches
            FROM pg_catalog.pg_constraint AS constraints
            WHERE constraints.conrelid = 'public.support_sites'::regclass
              AND constraints.conname = :constraint_name
            """
            ),
            {"constraint_name": _SUPPORT_SITE_TENANT_FK},
        )
        .scalar_one_or_none()
    )
    if row is False:
        raise RuntimeError(
            f"{_SUPPORT_SITE_TENANT_FK} already exists with an unexpected definition; "
            "inspect and repair that constraint before rerunning the migration."
        )
    return row is True


def _drop_temporary_migration_artifacts(*, drop_constraint: bool) -> None:
    drop_constraint_sql = (
        "EXECUTE 'ALTER TABLE public.support_sites "
        f"DROP CONSTRAINT IF EXISTS {_SUPPORT_SITE_TENANT_FK}';"
        if drop_constraint
        else ""
    )
    _run_autocommit(
        f"""
        DO $migration_cleanup$
        BEGIN
            EXECUTE 'DROP POLICY IF EXISTS {_MIGRATION_READ_POLICY} '
                    'ON public.support_sites';
            {drop_constraint_sql}
        END
        $migration_cleanup$;
        """
    )


def _add_and_validate_support_site_tenant_constraint() -> None:
    constraint_preexisting = _support_site_tenant_constraint_exists()
    if not constraint_preexisting:
        # NOT VALID makes this a short catalog operation. It starts enforcing
        # new writes immediately, which closes the race before online validation.
        _run_autocommit(
            f"""
            ALTER TABLE public.support_sites
            ADD CONSTRAINT {_SUPPORT_SITE_TENANT_FK}
            FOREIGN KEY (tenant_id)
            REFERENCES public.tenants (tenant_id)
            ON DELETE CASCADE
            NOT VALID
            """
        )
        _run_autocommit(
            f"""
            COMMENT ON CONSTRAINT {_SUPPORT_SITE_TENANT_FK}
            ON public.support_sites
            IS '{_SUPPORT_SITE_TENANT_FK_OWNER}'
            """
        )

    try:
        # FORCE RLS also applies to the table owner. A policy scoped only to the
        # current migrator lets PostgreSQL's validator inspect every existing row
        # without weakening access for app_tenant or changing FORCE RLS state.
        _run_autocommit(
            f"""
            DO $migration_policy$
            BEGIN
                EXECUTE 'DROP POLICY IF EXISTS {_MIGRATION_READ_POLICY} '
                        'ON public.support_sites';
                EXECUTE 'CREATE POLICY {_MIGRATION_READ_POLICY} '
                        'ON public.support_sites FOR SELECT TO CURRENT_USER '
                        'USING (true)';
            END
            $migration_policy$;
            """
        )
        _run_autocommit(
            f"""
            DO $migration_validation$
            DECLARE
                orphan_count bigint;
                orphan_sample text;
                validation_detail text;
            BEGIN
                BEGIN
                    EXECUTE 'ALTER TABLE public.support_sites '
                            'VALIDATE CONSTRAINT {_SUPPORT_SITE_TENANT_FK}';
                EXCEPTION
                    WHEN foreign_key_violation THEN
                        GET STACKED DIAGNOSTICS
                            validation_detail = PG_EXCEPTION_DETAIL;

                        WITH orphans AS MATERIALIZED (
                            SELECT sites.tenant_id,
                                   sites.site_id,
                                   sites.tenant_id || '/' || sites.site_id AS site_ref
                            FROM public.support_sites AS sites
                            LEFT JOIN public.tenants AS tenants
                              ON tenants.tenant_id = sites.tenant_id
                            WHERE tenants.tenant_id IS NULL
                        )
                        SELECT
                            (SELECT count(*) FROM orphans),
                            (
                                SELECT string_agg(
                                    sample.site_ref,
                                    ', ' ORDER BY sample.tenant_id, sample.site_id
                                )
                                FROM (
                                    SELECT tenant_id, site_id, site_ref
                                    FROM orphans
                                    ORDER BY tenant_id, site_id
                                    LIMIT 20
                                ) AS sample
                            )
                        INTO orphan_count, orphan_sample;

                        RAISE EXCEPTION USING
                            ERRCODE = '23503',
                            MESSAGE = format(
                                'Cannot validate {_SUPPORT_SITE_TENANT_FK}: '
                                'support_sites contains %s orphan tenant bindings.',
                                orphan_count
                            ),
                            DETAIL = format(
                                'Sample tenant/site bindings: %s. PostgreSQL detail: %s',
                                COALESCE(orphan_sample, '(unavailable)'),
                                COALESCE(validation_detail, '(unavailable)')
                            ),
                            HINT =
                                'Create the missing tenant records or quarantine/delete '
                                'the orphan sites, then rerun the migration.';
                END;
            END
            $migration_validation$;
            """
        )
        _drop_temporary_migration_artifacts(drop_constraint=False)
    except BaseException:
        # Restore the exact pre-migration state when validation (or temporary
        # policy cleanup) fails. A constraint left by an earlier run is retained.
        _drop_temporary_migration_artifacts(drop_constraint=not constraint_preexisting)
        raise


def upgrade() -> None:
    _add_and_validate_support_site_tenant_constraint()
    op.create_table(
        "platform_site_directory",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("site_id", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("verification_status", sa.String(length=30), nullable=False),
        sa.Column("verification_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("knowledge_publication_state", sa.String(length=30), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id"],
            ["support_sites.tenant_id", "support_sites.site_id"],
            name="fk_platform_site_directory_support_site",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "site_id", name="uq_platform_site_directory_tenant_site"),
    )
    for column in (
        "tenant_id",
        "site_id",
        "status",
        "verification_status",
        "verification_expires_at",
        "knowledge_publication_state",
        "source_updated_at",
        "updated_at",
    ):
        op.create_index(
            f"ix_platform_site_directory_{column}",
            "platform_site_directory",
            [column],
        )

    op.create_table(
        "platform_tenant_entitlements",
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("site_limit", sa.Integer(), nullable=True),
        sa.Column("plan_id", sa.String(length=80), nullable=True),
        sa.Column("subscription_status", sa.String(length=30), nullable=True),
        sa.Column("quota_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("subscription_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.tenant_id"],
            name="fk_platform_tenant_entitlements_tenant",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tenant_id"),
    )
    for column in ("plan_id", "subscription_status", "source_updated_at"):
        op.create_index(
            f"ix_platform_tenant_entitlements_{column}",
            "platform_tenant_entitlements",
            [column],
        )

    # Database-owned synchronization keeps projections correct while old and
    # new application revisions overlap during rolling deploys or rollback.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.sync_platform_site_directory_projection()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            INSERT INTO public.platform_site_directory
                (tenant_id, site_id, name, base_url, status,
                 verification_status, verification_expires_at,
                 knowledge_publication_state,
                 source_updated_at, created_at, updated_at)
            VALUES
                (NEW.tenant_id, NEW.site_id, NEW.name, NEW.base_url, NEW.status,
                 NEW.verification_status, NEW.verification_expires_at,
                 NEW.knowledge_publication_state,
                 NEW.updated_at, NEW.created_at, NEW.updated_at)
            ON CONFLICT (tenant_id, site_id) DO UPDATE SET
                name = EXCLUDED.name,
                base_url = EXCLUDED.base_url,
                status = EXCLUDED.status,
                verification_status = EXCLUDED.verification_status,
                verification_expires_at = EXCLUDED.verification_expires_at,
                knowledge_publication_state = EXCLUDED.knowledge_publication_state,
                source_updated_at = EXCLUDED.source_updated_at,
                created_at = EXCLUDED.created_at,
                updated_at = EXCLUDED.updated_at;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.sync_platform_site_directory_projection() FROM PUBLIC"
    )
    op.execute(
        """
        CREATE TRIGGER trg_support_sites_platform_directory
        AFTER INSERT OR UPDATE OF
            name, base_url, status, verification_status,
            verification_expires_at, knowledge_publication_state, updated_at
        ON support_sites
        FOR EACH ROW
        EXECUTE FUNCTION public.sync_platform_site_directory_projection();
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.sync_platform_tenant_quota_projection()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                UPDATE public.platform_tenant_entitlements
                SET site_limit = NULL,
                    quota_updated_at = NULL,
                    source_updated_at = subscription_updated_at,
                    updated_at = subscription_updated_at
                WHERE tenant_id = OLD.tenant_id
                  AND subscription_updated_at IS NOT NULL;
                IF NOT FOUND THEN
                    DELETE FROM public.platform_tenant_entitlements
                    WHERE tenant_id = OLD.tenant_id;
                END IF;
                RETURN OLD;
            END IF;

            INSERT INTO public.platform_tenant_entitlements
                (tenant_id, site_limit, plan_id, subscription_status,
                 quota_updated_at, subscription_updated_at,
                 source_updated_at, created_at, updated_at)
            VALUES
                (NEW.tenant_id, NEW.site_limit, NULL, NULL,
                 NEW.updated_at, NULL, NEW.updated_at, NEW.created_at, NEW.updated_at)
            ON CONFLICT (tenant_id) DO UPDATE SET
                site_limit = EXCLUDED.site_limit,
                quota_updated_at = EXCLUDED.quota_updated_at,
                source_updated_at = GREATEST(
                    EXCLUDED.quota_updated_at,
                    COALESCE(
                        platform_tenant_entitlements.subscription_updated_at,
                        EXCLUDED.quota_updated_at
                    )
                ),
                created_at = LEAST(
                    platform_tenant_entitlements.created_at,
                    EXCLUDED.created_at
                ),
                updated_at = GREATEST(
                    EXCLUDED.quota_updated_at,
                    COALESCE(
                        platform_tenant_entitlements.subscription_updated_at,
                        EXCLUDED.quota_updated_at
                    )
                );
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute("REVOKE ALL ON FUNCTION public.sync_platform_tenant_quota_projection() FROM PUBLIC")
    op.execute(
        """
        CREATE TRIGGER trg_tenant_quotas_platform_entitlement
        AFTER INSERT OR UPDATE OF site_limit, updated_at OR DELETE
        ON tenant_quotas
        FOR EACH ROW
        EXECUTE FUNCTION public.sync_platform_tenant_quota_projection();
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.sync_platform_tenant_subscription_projection()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                UPDATE public.platform_tenant_entitlements
                SET plan_id = NULL,
                    subscription_status = NULL,
                    subscription_updated_at = NULL,
                    source_updated_at = quota_updated_at,
                    updated_at = quota_updated_at
                WHERE tenant_id = OLD.tenant_id
                  AND quota_updated_at IS NOT NULL;
                IF NOT FOUND THEN
                    DELETE FROM public.platform_tenant_entitlements
                    WHERE tenant_id = OLD.tenant_id;
                END IF;
                RETURN OLD;
            END IF;

            INSERT INTO public.platform_tenant_entitlements
                (tenant_id, site_limit, plan_id, subscription_status,
                 quota_updated_at, subscription_updated_at,
                 source_updated_at, created_at, updated_at)
            VALUES
                (NEW.tenant_id, NULL, NEW.plan_id, NEW.status,
                 NULL, NEW.updated_at, NEW.updated_at, NEW.created_at, NEW.updated_at)
            ON CONFLICT (tenant_id) DO UPDATE SET
                plan_id = EXCLUDED.plan_id,
                subscription_status = EXCLUDED.subscription_status,
                subscription_updated_at = EXCLUDED.subscription_updated_at,
                source_updated_at = GREATEST(
                    EXCLUDED.subscription_updated_at,
                    COALESCE(
                        platform_tenant_entitlements.quota_updated_at,
                        EXCLUDED.subscription_updated_at
                    )
                ),
                created_at = LEAST(
                    platform_tenant_entitlements.created_at,
                    EXCLUDED.created_at
                ),
                updated_at = GREATEST(
                    EXCLUDED.subscription_updated_at,
                    COALESCE(
                        platform_tenant_entitlements.quota_updated_at,
                        EXCLUDED.subscription_updated_at
                    )
                );
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.sync_platform_tenant_subscription_projection() FROM PUBLIC"
    )
    op.execute(
        """
        CREATE TRIGGER trg_tenant_subscriptions_platform_entitlement
        AFTER INSERT OR UPDATE OF plan_id, status, updated_at OR DELETE
        ON tenant_subscriptions
        FOR EACH ROW
        EXECUTE FUNCTION public.sync_platform_tenant_subscription_projection();
        """
    )

    # RLS applies to support_sites, so backfill one trusted tenant at a time.
    op.execute(
        """
        DO $$
        DECLARE current_tenant text;
        BEGIN
            FOR current_tenant IN SELECT tenant_id FROM tenants LOOP
                PERFORM set_config('app.tenant_id', current_tenant, true);
                INSERT INTO platform_site_directory
                    (tenant_id, site_id, name, base_url, status,
                     verification_status, verification_expires_at,
                     knowledge_publication_state,
                     source_updated_at, created_at, updated_at)
                SELECT tenant_id, site_id, name, base_url, status,
                       verification_status, verification_expires_at,
                       knowledge_publication_state,
                       updated_at, created_at, updated_at
                FROM support_sites
                WHERE tenant_id = current_tenant
                ON CONFLICT (tenant_id, site_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    base_url = EXCLUDED.base_url,
                    status = EXCLUDED.status,
                    verification_status = EXCLUDED.verification_status,
                    verification_expires_at = EXCLUDED.verification_expires_at,
                    knowledge_publication_state = EXCLUDED.knowledge_publication_state,
                    source_updated_at = EXCLUDED.source_updated_at,
                    created_at = EXCLUDED.created_at,
                    updated_at = EXCLUDED.updated_at;

                INSERT INTO platform_tenant_entitlements
                    (tenant_id, site_limit, plan_id, subscription_status,
                     quota_updated_at, subscription_updated_at,
                     source_updated_at, created_at, updated_at)
                SELECT tenants.tenant_id, quotas.site_limit,
                       subscriptions.plan_id, subscriptions.status,
                       quotas.updated_at, subscriptions.updated_at,
                       CASE
                           WHEN quotas.updated_at IS NULL THEN subscriptions.updated_at
                           WHEN subscriptions.updated_at IS NULL THEN quotas.updated_at
                           ELSE GREATEST(quotas.updated_at, subscriptions.updated_at)
                       END,
                       CASE
                           WHEN quotas.created_at IS NULL THEN subscriptions.created_at
                           WHEN subscriptions.created_at IS NULL THEN quotas.created_at
                           ELSE LEAST(quotas.created_at, subscriptions.created_at)
                       END,
                       CASE
                           WHEN quotas.updated_at IS NULL THEN subscriptions.updated_at
                           WHEN subscriptions.updated_at IS NULL THEN quotas.updated_at
                           ELSE GREATEST(quotas.updated_at, subscriptions.updated_at)
                       END
                FROM tenants
                LEFT JOIN tenant_quotas AS quotas
                    ON quotas.tenant_id = tenants.tenant_id
                LEFT JOIN tenant_subscriptions AS subscriptions
                    ON subscriptions.tenant_id = tenants.tenant_id
                WHERE tenants.tenant_id = current_tenant
                  AND (quotas.tenant_id IS NOT NULL OR subscriptions.tenant_id IS NOT NULL)
                ON CONFLICT (tenant_id) DO UPDATE SET
                    site_limit = EXCLUDED.site_limit,
                    plan_id = EXCLUDED.plan_id,
                    subscription_status = EXCLUDED.subscription_status,
                    quota_updated_at = EXCLUDED.quota_updated_at,
                    subscription_updated_at = EXCLUDED.subscription_updated_at,
                    source_updated_at = EXCLUDED.source_updated_at,
                    created_at = EXCLUDED.created_at,
                    updated_at = EXCLUDED.updated_at;
            END LOOP;
        END $$;
        """
    )
    # The shared application role may read these control-plane projections,
    # but only trusted source-table triggers are allowed to mutate them.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_tenant') THEN
                EXECUTE 'REVOKE INSERT, UPDATE, DELETE ON TABLE '
                    'public.platform_site_directory, '
                    'public.platform_tenant_entitlements FROM app_tenant';
                EXECUTE 'REVOKE ALL ON SEQUENCE '
                    'public.platform_site_directory_id_seq FROM app_tenant';
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_tenant_subscriptions_platform_entitlement "
        "ON tenant_subscriptions"
    )
    op.execute("DROP FUNCTION IF EXISTS public.sync_platform_tenant_subscription_projection()")
    op.execute("DROP TRIGGER IF EXISTS trg_tenant_quotas_platform_entitlement ON tenant_quotas")
    op.execute("DROP FUNCTION IF EXISTS public.sync_platform_tenant_quota_projection()")
    op.execute("DROP TRIGGER IF EXISTS trg_support_sites_platform_directory ON support_sites")
    op.execute("DROP FUNCTION IF EXISTS public.sync_platform_site_directory_projection()")
    for column in ("source_updated_at", "subscription_status", "plan_id"):
        op.drop_index(
            f"ix_platform_tenant_entitlements_{column}",
            table_name="platform_tenant_entitlements",
        )
    op.drop_table("platform_tenant_entitlements")
    for column in (
        "updated_at",
        "source_updated_at",
        "knowledge_publication_state",
        "verification_expires_at",
        "verification_status",
        "status",
        "site_id",
        "tenant_id",
    ):
        op.drop_index(f"ix_platform_site_directory_{column}", table_name="platform_site_directory")
    op.drop_table("platform_site_directory")
    op.execute(
        f"""
        DO $downgrade_constraint$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM pg_catalog.pg_constraint AS constraints
                WHERE constraints.conrelid = 'public.support_sites'::regclass
                  AND constraints.conname = '{_SUPPORT_SITE_TENANT_FK}'
                  AND pg_catalog.obj_description(
                      constraints.oid,
                      'pg_constraint'
                  ) = '{_SUPPORT_SITE_TENANT_FK_OWNER}'
            ) THEN
                EXECUTE 'ALTER TABLE public.support_sites '
                        'DROP CONSTRAINT {_SUPPORT_SITE_TENANT_FK}';
            END IF;
        END
        $downgrade_constraint$;
        """
    )
