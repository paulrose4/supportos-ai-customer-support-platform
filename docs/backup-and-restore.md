# Backup and Restore

## Data ownership

- PostgreSQL is authoritative for customers, conversations, messages, tickets, handoffs, administrator identities, sessions, audit records, knowledge control state, and sync state.
- Qdrant is a rebuildable knowledge-vector projection. It never stores orders, payments, authentication records, or authoritative customer state.

Keep backups outside the application host or replicate them to encrypted storage. A backup that remains only on the same disk is not a disaster-recovery copy.

## PostgreSQL backup

PostgreSQL backups are online, transactionally consistent custom-format dumps:

```bash
./scripts/backup_postgres.sh
```

The command writes a timestamped file under `backups/postgres/`. It is read-only, requires database availability, and does not modify application state.

Restore is destructive and therefore requires the explicit `--confirm` flag:

```bash
./scripts/restore_postgres.sh --confirm backups/postgres/postgres-YYYYMMDDTHHMMSSZ.dump
```

The restore procedure stops the API, force-drops the configured application database, restores the dump, reapplies forward migrations, and restarts the API. Schedule human approval and a maintenance window. Preserve the pre-restore backup and logs in the change record.

## Qdrant backup

The current script performs a consistent cold backup of the Qdrant storage volume:

```bash
./scripts/backup_qdrant.sh
```

It stops Qdrant, archives the fixed `/qdrant/storage` volume through an ephemeral Alpine container, and restarts Qdrant with a shell trap even when archiving fails. Knowledge retrieval is temporarily unavailable, so the application must fail closed to human handoff during the window.

Restore is destructive and requires explicit confirmation:

```bash
./scripts/restore_qdrant.sh --confirm backups/qdrant/qdrant-YYYYMMDDTHHMMSSZ.tar.gz
```

The restore deletes only the fixed Qdrant storage-volume contents while Qdrant is stopped, extracts the selected archive, and restarts Qdrant. Never pass an untrusted archive. Prefer rebuilding Qdrant from published PostgreSQL knowledge manifests when that path is available and validated.

## Recommended schedule

- PostgreSQL: nightly plus before every migration or release.
- Qdrant: after a reviewed bulk knowledge publication and before Qdrant upgrades.
- Restore drill: monthly in an isolated environment.
- Retention: define daily/weekly/monthly retention after legal and privacy review.

## Verification and audit

For each backup, record UTC timestamp, environment, operator, application version, database migration head, file size, and SHA-256 checksum. A restore is not considered verified until readiness passes and a tenant-isolation smoke test succeeds.


## Windows commands

```powershell
pwsh -File scripts/backup_postgres.ps1 -EnvFile .env.production
pwsh -File scripts/backup_qdrant.ps1 -EnvFile .env.production
pwsh -File scripts/restore_postgres.ps1 -EnvFile .env.production -BackupFile <path> -ConfirmRestore
pwsh -File scripts/restore_qdrant.ps1 -EnvFile .env.production -BackupFile <path> -ConfirmRestore
```

The PowerShell restore commands enforce the same explicit confirmation and fixed-volume boundaries as the Linux scripts.

## Machine-readable status

Successful backup scripts atomically update `backups/status/postgres.json` and `backups/status/qdrant.json` with completion time, safe file name, byte size, SHA-256 and restore-verification timestamp. Production mounts only this directory into the API as read-only. Dashboard Settings reports missing, stale or current status using `BACKUP_MAX_AGE_HOURS`; it never opens the backup artifact itself.

After an isolated restore drill passes readiness and tenant-isolation acceptance, mark the corresponding status with a trusted operator and approval reference:

```bash
python scripts/mark_backup_verified.py \
  --artifact-type postgres \
  --actor-subject-id operations-owner \
  --approval-reference RESTORE-42
```

The status update is atomic and desired-state idempotent for the same artifact, actor and approval reference. It changes only backup metadata and requires the restore operator's explicit approval; it never modifies application business records.
