# Staging Promotion Runbook

Use this runbook to promote a verified release manifest into a staging environment.
Promotion is dry-run by default.

## Preconditions

- A release manifest exists in `dist/releases/<version>.json`.
- The manifest's `cyber-team-core:<version>` and `cyber-team-ui:<version>` images exist
  locally or have been pulled onto the staging host.
- A staging environment file exists, for example `.env.staging`.
- The working tree contains the same compose file version used to create the release
  manifest.

## Dry Run

```bash
RELEASE_VERSION=<version> \
STAGING_ENV_FILE=.env.staging \
./scripts/promote-staging.sh
```

The dry run prints the backup, image validation, compose, and smoke-test commands that
would execute.

## Execute Promotion

```bash
RELEASE_VERSION=<version> \
STAGING_ENV_FILE=.env.staging \
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

## Useful Switches

- `RELEASE_MANIFEST=/path/to/manifest.json` uses an explicit manifest path.
- `RUN_BACKUP=0` skips the pre-promotion backup.
- `RUN_COMPOSE_SMOKE=0` skips the smoke test.
- `BACKUP_DIR=/path/to/backups` changes where copied backups are stored.

## Safety Rules

- Do not promote a release whose manifest does not show passing quality gate,
  migration rehearsal, and image scan checks.
- Use `PROMOTE_DRY_RUN=0` only on the staging host or an isolated staging environment.
- Keep the release manifest, staging backup, and smoke-test output together for rollback.
