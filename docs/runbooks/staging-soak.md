# Staging Soak Runbook

Cyber-Team uses a strict elapsed-time soak after release-candidate promotion. The
monitor checks the public health endpoint, owner login, operations readiness,
release version, and build SHA. Every observation is appended to JSON Lines evidence;
state and final summary files are updated atomically. Credentials are read from the
ignored staging environment file and are never written to evidence.

## Start the 24-hour gate

```bash
./scripts/start-staging-soak.sh
```

The launcher uses the exact image running in `cyberteam-staging-core`, host networking,
a read-only environment mount, and a writable `dist/soak` evidence mount. The detached
container is named `cyberteam-staging-soak` and removes itself after completion.

Fresh signals, events, claim-extraction retries, and outcome assessments that remain
inside their configured processing windows are healthy processing. Stale pending
signals, unexplained events, expired leases, stale extraction failures, stale
unassessed outcomes, or expired model qualifications fail the sample.

## Inspect progress

```bash
docker logs cyberteam-staging-soak
jq . dist/soak/staging-soak-*.state.json
```

The default cadence is five minutes for 24 hours. Override it only for a preflight:

```bash
SOAK_CONTAINER_NAME=cyberteam-staging-soak-preflight \
SOAK_DURATION_SECONDS=30 \
SOAK_INTERVAL_SECONDS=5 \
./scripts/start-staging-soak.sh
```

## Acceptance

The final summary must report `status=passed`, no failed samples, the expected release
version and build SHA, and no health/readiness blocker during the elapsed window. An
interrupted container or any failed probe fails the soak; restart a complete 24-hour
window after fixing the cause.
