# Staging Promotion Runbook

Use this runbook to promote a verified release manifest into a staging environment.
Promotion is dry-run by default. The staging wrapper delegates to the general
deployment promotion workflow in `scripts/promote-release.sh`.

## Preconditions

- A release manifest exists in `dist/releases/<version>.json`.
- The manifest's `cyber-team-core:<version>` and `cyber-team-ui:<version>` images exist
  locally or have been pulled onto the staging host.
- A staging environment file exists, for example `deploy/environments/staging.env`.
- The working tree contains the same compose file version used to create the release
  manifest.

## Dry Run

```bash
RELEASE_VERSION=<version> \
./scripts/promote-staging.sh
```

The dry run prints the backup, image validation, compose, and smoke-test commands that
would execute.

## Execute Promotion

```bash
RELEASE_VERSION=<version> \
PROMOTE_DRY_RUN=0 \
./scripts/promote-staging.sh
```

By default, the script:

- Reads image tags from the release manifest.
- Loads variables from the staging environment file.
- Takes a PostgreSQL custom-format backup into `backups/staging`.
- Verifies the release images exist locally.
- Starts the staging stack with `--no-build` so it runs the release artifacts.
- Runs the Compose smoke test against the already-started stack.

## Restart Current Promoted Staging Stack

If the staging containers were intentionally stopped and you only need to bring the
same promoted release back online, use the current-release restart helper instead
of a raw `docker compose up`.

```bash
./scripts/start-staging-current.sh
START_STAGING_DRY_RUN=0 ./scripts/start-staging-current.sh
```

The helper reads the latest promotion record in `dist/promotions/staging`, exports
the recorded `CORE_IMAGE`, `UI_IMAGE`, `APP_VERSION`, `BUILD_SHA`, and
`COMPOSE_PROJECT_NAME`, and starts the full staging stack including the ERPNext
profile. This prevents direct Compose restarts from accidentally falling back to
`cyber-team-core:latest`, `cyber-team-ui:latest`, or `BUILD_SHA=local`.

## Useful Switches

- `RELEASE_MANIFEST=/path/to/manifest.json` uses an explicit manifest path.
- `STAGING_ENV_FILE=/path/to/env` or `PROMOTE_ENV_FILE=/path/to/env` overrides the
  staging environment file.
- `DEPLOYMENT_MANIFEST=/path/to/staging.json` uses a custom deployment manifest.
- `RUN_BACKUP=0` skips the pre-promotion backup.
- `RUN_COMPOSE_SMOKE=0` skips the smoke test.
- `BACKUP_DIR=/path/to/backups` changes where copied backups are stored.

## Safety Rules

- Do not promote a release whose manifest does not show passing quality gate,
  migration rehearsal, and image scan checks.
- Use `PROMOTE_DRY_RUN=0` only on the staging host or an isolated staging environment.
- Keep the release manifest, staging backup, and smoke-test output together for rollback.

See `deployment-promotion.md` for production approval requirements and promotion
record details.
