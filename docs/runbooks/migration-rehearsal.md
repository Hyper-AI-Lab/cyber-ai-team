# Migration Rehearsal Runbook

Cyber-Team uses Alembic migrations for PostgreSQL schema changes. Before shipping
schema changes, rehearse them against both empty databases and representative legacy
schemas.

## Automated Legacy Rehearsal

Run:

```bash
BACKEND_VENV=/tmp/cyberteam-venv ./scripts/migration-rehearsal.sh
```

The rehearsal script:

- Starts a disposable PostgreSQL 16 container on `127.0.0.1:55433`.
- Loads `scripts/sql/pre-alembic-approval-schema.sql`, which models the legacy
  pre-Alembic approval schema.
- Runs `alembic upgrade head`.
- Verifies the Alembic revision, legacy row preservation, approval column defaults,
  removal of the old approval foreign key, and creation of the new core tables.
- Removes the disposable container by default.

Useful options:

- `MIGRATION_REHEARSAL_PORT=55434` changes the local PostgreSQL port.
- `MIGRATION_REHEARSAL_CLEANUP=0` keeps the container for manual inspection.
- `ALEMBIC_BIN=/path/to/alembic` overrides the Alembic executable.

## Manual Staging Rehearsal

For a production-like rehearsal:

1. Restore a sanitized production backup into an isolated staging PostgreSQL instance.
2. Point backend settings at that staging database.
3. Run `alembic current` and record the starting revision or legacy state.
4. Run `alembic upgrade head`.
5. Run application smoke checks, including login, dashboard, memory recall, approvals,
   and workflow execution.
6. Compare row counts and critical constraints before and after.
7. Record runtime, lock duration, errors, and rollback/forward plan.

Never run rehearsals against production directly.

## Rollback Policy

The initial migration downgrade is destructive because it drops all Cyber-Team tables.
Use it only for disposable local or CI databases. Production rollback should restore
from backup or roll forward with a corrective migration.
