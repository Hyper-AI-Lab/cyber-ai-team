# Deployment Promotion Runbook

Use this runbook to promote a verified release manifest into staging or production.
Promotion is dry-run by default and writes an audit record after successful execution.

## Deployment Manifests

Environment manifests live in `deploy/manifests`:

- `staging.json` uses `deploy/environments/staging.env`.
- `production.json` uses `deploy/environments/production.env` and requires approval.

Copy the matching environment example before first use:

```bash
cp deploy/environments/staging.env.example deploy/environments/staging.env
cp deploy/environments/production.env.example deploy/environments/production.env
```

The real `.env` files are ignored by Git. Replace every placeholder secret, port, URL,
and owner credential before execution.

## Dry Run

```bash
PROMOTE_ENVIRONMENT=staging \
RELEASE_VERSION=<version> \
./scripts/promote-release.sh
```

For production:

```bash
PROMOTE_ENVIRONMENT=production \
RELEASE_VERSION=<version> \
./scripts/promote-release.sh
```

Dry runs validate the deployment manifest, release manifest, required release checks,
environment file path, and command plan. They do not require production approval unless
an approval file is supplied.

## Production Approval

Production execution requires either an approval JSON file:

```json
{
  "environment": "production",
  "version": "<version>",
  "approved_by": "ops@example.com",
  "change_ticket": "CHG-1234",
  "approved_at": "2026-05-27T12:00:00Z",
  "release_manifest_sha256": "<optional-sha256>",
  "expires_at": "2026-05-28T12:00:00Z"
}
```

or explicit environment variables:

```bash
PROMOTION_APPROVER=ops@example.com \
PROMOTION_CHANGE_TICKET=CHG-1234 \
PROMOTION_APPROVED_AT=2026-05-27T12:00:00Z \
PROMOTE_ENVIRONMENT=production \
PROMOTE_DRY_RUN=0 \
RELEASE_VERSION=<version> \
./scripts/promote-release.sh
```

Use `PROMOTION_APPROVAL_FILE=/path/to/approval.json` when using the approval file.
If `release_manifest_sha256` is present, it must match the release manifest exactly.

## Execute Promotion

```bash
PROMOTE_ENVIRONMENT=staging \
PROMOTE_DRY_RUN=0 \
RELEASE_VERSION=<version> \
./scripts/promote-release.sh
```

By default, execution:

- Validates that required release checks passed in the release manifest.
- Loads the target environment file.
- Takes a PostgreSQL custom-format backup.
- Verifies the release images exist locally.
- Starts the configured Compose services with `--no-build`.
- Runs the Compose smoke test against the already-started stack.
- Writes a promotion record to `dist/promotions/<environment>`.

## Useful Switches

- `DEPLOYMENT_MANIFEST=/path/to/env.json` uses a custom deployment manifest.
- `PROMOTE_ENV_FILE=/path/to/env` overrides the manifest environment file.
- `PROMOTION_RECORD_DIR=/path/to/records` changes promotion record output.
- `RUN_BACKUP=0` skips the pre-promotion PostgreSQL backup.
- `RUN_COMPOSE_SMOKE=0` skips the post-promotion smoke test.
- `PROMOTE_SERVICES="postgres redis core ui"` overrides the service list.
- `PROMOTE_ALLOW_INCOMPLETE_CHECKS=1` bypasses missing release checks for emergency
  recovery only; record the reason in the change ticket.

## Safety Rules

- Do not execute production promotion without a fresh backup and approval record.
- Keep the release manifest, approval record, backup artifact, and promotion record
  together for rollback.
- Production environment files must set `ENVIRONMENT=production`,
  `COMMUNICATIONS_ALLOW_SIMULATION=false`, `OWNER_PASSWORD_HASH`, non-default datastore
  passwords, and a specific `CORS_ALLOWED_ORIGINS` value.
- For production smoke tests, provide `OWNER_PASSWORD` as a runtime environment
  variable for the smoke client; do not store it in `production.env`.
- Run rollback dry runs before risky promotions.
