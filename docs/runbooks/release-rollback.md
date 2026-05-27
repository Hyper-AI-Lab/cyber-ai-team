# Release and Rollback Runbook

Use this runbook to prepare a Cyber-Team release candidate and rehearse rollback
steps before promoting a build.

## Release Candidate Check

Run the release check from a clean working tree:

```bash
RELEASE_VERSION=2026.05.26-1 ./scripts/release-check.sh
```

By default the script:

- Refuses to run if the Git working tree is dirty.
- Runs the normal quality gate.
- Runs the disposable migration rehearsal.
- Builds version-tagged `cyber-team-core` and `cyber-team-ui` Docker images.
- Scans the built images for high/critical vulnerabilities.
- Writes a release manifest to `dist/releases/<version>.json`.

Optional switches:

- `RUN_COMPOSE_SMOKE=1` also runs the full Docker Compose smoke test.
- `RUN_IMAGE_SCAN=0` skips image scanning.
- `BUILD_IMAGES=0` skips image builds.
- `RUN_QUALITY_GATE=0` skips the quality gate when it already ran in CI.
- `RELEASE_ALLOW_DIRTY=1` allows local experiment manifests from a dirty tree.

Use `scripts/promote-release.sh` with `PROMOTE_ENVIRONMENT=staging` or
`PROMOTE_ENVIRONMENT=production` to deploy a verified manifest. Production promotion
requires approval and writes a promotion record.

## Rollback Dry Run

Rollback is dry-run by default:

```bash
ROLLBACK_TARGET=<previous-tag-or-sha> ./scripts/rollback.sh
```

The dry run prints the exact Git, Docker Compose, optional restore, and smoke-test
commands that would execute.

## Executing a Rollback

After confirming the plan and required backup:

```bash
ROLLBACK_TARGET=<previous-tag-or-sha> \
ROLLBACK_DRY_RUN=0 \
RESTORE_POSTGRES_BACKUP=/backups/cyberteam-pre-release.dump \
./scripts/rollback.sh
```

If `RESTORE_POSTGRES_BACKUP` is omitted, the rollback only checks out the target
code, rebuilds the core/UI images, restarts the stack, and runs the smoke test.

## Safety Rules

- Never restore over production until the restore has been rehearsed in staging.
- Keep the release manifest and backup artifact together.
- Prefer rolling forward with a corrective migration when data has already been
  mutated by a released version.
- Run `COMPOSE_SMOKE_CLEANUP=1 ./scripts/compose-smoke.sh` after any rollback or
  promotion to verify login, readiness, approval replay, and communication logs.
