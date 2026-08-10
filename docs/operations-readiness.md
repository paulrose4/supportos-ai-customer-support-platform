# Operations Readiness

## Administrator governance

The Dashboard exposes tenant-scoped audit events to principals with `audit:read`. Results use cursor pagination and recursively replace password, token, credential, cookie, API-key, secret, and site-key fields with `[REDACTED]`. Reading audit data is side-effect free and deliberately does not create another audit event.

Every session-authenticated administrator can list their own recent sessions and revoke one session at a time. Session source data is a one-way fingerprint prefix, not a raw address. Revocation is desired-state idempotent, limited to the authenticated tenant and user, transactional in PostgreSQL, and audited only on the first state change. Revoking the current session deletes the browser cookie. Dashboard confirmation is the human approval boundary.

## Backup status

Successful PostgreSQL and Qdrant backup scripts create an atomic metadata pointer in `backups/status/postgres.json` or `backups/status/qdrant.json`. The API mounts only this metadata directory read-only. It never reads backup contents. Dashboard system status marks each backup as:

- `current`: completed within `BACKUP_MAX_AGE_HOURS`;
- `stale`: present but older than the configured threshold;
- `missing`: no valid manifest is available.

Backup creation is an operator-initiated infrastructure write. Each run creates an immutable timestamped artifact, validates that it is non-empty, calculates SHA-256, then atomically replaces only the corresponding status pointer. It requires host backup permissions and operator approval under the deployment runbook. It does not modify application business records.

## Retention

Retention execution is disabled by default. Preview is read-only:

```bash
python -m scripts.run_retention --tenant-id tenant-example
```

Execution requires all of the following:

1. an approved policy version and durations;
2. `RETENTION_EXECUTION_ENABLED=true`;
3. an explicit tenant identifier;
4. `--execute`;
5. a unique `--run-key`;
6. a trusted `--actor-subject-id`;
7. a human `--approval-reference`.

Example after privacy approval:

```bash
python -m scripts.run_retention \
  --tenant-id tenant-example \
  --execute \
  --run-key 2026-07-16-tenant-example \
  --actor-subject-id privacy-owner \
  --approval-reference PRIVACY-42
```

The write is tenant-scoped, protected by a PostgreSQL transaction and advisory lock, and idempotent by tenant plus run key. It deletes only sessions that have been revoked or expired beyond policy, expired customer memory beyond its grace period, old support-operation idempotency records, and old non-retention audit events. It then writes `retention.executed` with policy, approval and deletion counts. Retention audit events are excluded from automated deletion so retries remain provable. Legal-hold and final statutory durations remain launch approval inputs; keep execution disabled until those are confirmed.

## Launch acceptance

Before deployment, run the deterministic customer-support release gate:

```bash
python -m evals.run_production_gate
```

The versioned dataset is `evals/datasets/production_support.jsonl`; thresholds are in
`evals/release-gates.json`. The gate blocks unsupported numeric claims and citations, internal
system language, cross-tenant tokens, incorrect intent/action planning, missing required content,
and responses outside the intent-specific length contract. New production incidents and material
human edits must become regression cases before the next release.

This gate validates deterministic behavior and curated responses. It does not replace a staging
run against the configured model, store APIs, PostgreSQL, and Qdrant. Price, stock, discounts,
orders, tracking, cancellation eligibility, and refunds require live merchant-system adapters;
do not approve production based on crawled website facts for those fields.

The expanded model, retrieval, latency, automatic-resolution, handoff, and CSAT gates are defined
in `docs/enterprise-quality-gates.md`. Use `python -m scripts.release_quality_report` for the
combined fail-closed report.

Run the non-destructive acceptance script after every deployment. Passwords should come from an environment variable rather than command history:

```bash
export LAUNCH_ACCEPTANCE_PASSWORD='set-outside-shell-history'
python scripts/launch_acceptance.py \
  --base-url https://support.example.com \
  --tenant-id tenant-example \
  --username owner \
  --password-env LAUNCH_ACCEPTANCE_PASSWORD \
  --require-production \
  --require-current-backups
```

The script waits through bounded cold starts and checks liveness, readiness, Dashboard clickjacking/CSP headers, administrator login, trusted tenant identity, production provider configuration, dependency health, backup freshness, current-session visibility, audit redaction and logout. Supplying `--site-key` additionally verifies a valid signed presence heartbeat and rejection of an invalid key. It never sends a chat prompt and therefore does not consume model tokens.

## WordPress diagnostics

Plugin version 0.3.0 adds **Settings → Product Support Agent → Test connection**. The test performs a server-to-server signed presence update. It does not call the LLM, expose the site key to the browser, or create a durable business record. The presence write is an idempotent overwrite for the fixed diagnostic visitor, authorized by the site key, held in process memory and expires automatically. No separate human approval is required because the administrator explicitly initiates the reversible diagnostic.

Use `scripts/mark_backup_verified.py` after a successful isolated restore drill and launch acceptance. The metadata write is atomic, requires a trusted actor and approval reference, and is idempotent for an exact retry.
