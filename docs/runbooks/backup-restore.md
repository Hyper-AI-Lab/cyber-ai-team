# Backup and Restore Runbook

This runbook covers PostgreSQL and Qdrant data protection for Cyber-Team.
ERPNext has its own backup and restore drill flow because it owns MariaDB plus
site files; see `docs/runbooks/erpnext.md`.

## Scope

PostgreSQL stores canonical application data: agents, workflows, approvals, audit
events, communication logs, role manifests, and memory records. Qdrant stores semantic
memory vectors that can be degraded or rebuilt from PostgreSQL content in many cases,
but it should still be backed up for fast recovery.

## PostgreSQL Backup

Create a compressed custom-format backup:

```bash
docker compose exec postgres pg_dump \
  -U "${POSTGRES_USER:-cyberteam}" \
  -d "${POSTGRES_DB:-cyberteam}" \
  --format=custom \
  --file=/tmp/cyberteam-$(date +%Y%m%d-%H%M%S).dump
```

Copy it out of the container:

```bash
docker compose cp postgres:/tmp/cyberteam-YYYYMMDD-HHMMSS.dump ./backups/
```

Recommended cadence:

- Production: at least daily full backups plus point-in-time recovery if available.
- Before migrations: take a fresh full backup and verify it can be listed/restored.
- Retention: keep daily backups for 14 days and monthly backups according to customer
  or compliance requirements.

## PostgreSQL Restore Drill

Restore into an isolated database first:

```bash
createdb cyberteam_restore_test
pg_restore \
  --clean \
  --if-exists \
  --dbname cyberteam_restore_test \
  ./backups/cyberteam-YYYYMMDD-HHMMSS.dump
```

Then verify:

- `alembic current` reports the expected revision.
- Key table row counts are plausible.
- Owner login, dashboard KPIs, approval queue, and memory recall work against the
  restored database.

Do not restore over production until the restore has been rehearsed and the outage
plan is approved.

For staging, run the automated drill against the latest staging backup artifact:

```bash
BACKEND_VENV=/tmp/cyberteam-verify-venv ./scripts/staging-restore-drill.sh
```

The script starts an isolated PostgreSQL 16 container, restores the custom-format
backup, verifies Alembic state and key table row counts, removes the container, and
writes JSON evidence to:

```text
dist/restore-drills/staging/staging-restore-drill-YYYYMMDDTHHMMSSZ.json
```

## Qdrant Backup

Create a collection snapshot:

```bash
curl -X POST "http://localhost:6333/collections/cyberteam_memory/snapshots"
```

List snapshots:

```bash
curl "http://localhost:6333/collections/cyberteam_memory/snapshots"
```

Copy snapshots from the Qdrant volume or configure object storage according to the
deployment environment.

## Qdrant Restore

Restore the snapshot into a staging Qdrant instance first, then run memory recall smoke
checks. If Qdrant is unavailable or restore fails, the application should continue with
the PostgreSQL fallback path, with degraded semantic ranking.

## ERPNext Backup and Restore

ERPNext stores CRM, accounting, project, support, and procurement records in its
own MariaDB database and site file volumes. Run the ERPNext-specific automation
before ERPNext changes or staging promotion:

```bash
ERPNEXT_ENV_FILE=deploy/environments/staging.env ./scripts/erpnext-backup.sh
ERPNEXT_ENV_FILE=deploy/environments/staging.env ./scripts/erpnext-restore-drill.sh
```

ERPNext backup manifests are written under `backups/erpnext/staging/`.
Restore drill evidence is written under `dist/erpnext/restore-drills/`.

## Incident Checklist

- Stop write traffic if data corruption is suspected.
- Capture current logs and metrics before restarting services.
- Identify the last known good PostgreSQL backup and Qdrant snapshot.
- Restore to staging and verify application smoke tests.
- Communicate expected downtime and recovery point.
- Restore production or roll forward with a corrective migration.
- Record the incident timeline, root cause, and follow-up tests.
