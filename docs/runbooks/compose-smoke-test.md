# Docker Compose Smoke Test Runbook

Use this runbook to verify that a built Cyber-Team stack can boot and complete a
minimal owner workflow.

## What It Verifies

`scripts/compose-smoke.sh` starts the core Compose services and runs
`scripts/compose-smoke.py`, which checks:

- API health endpoint returns `ok` and readiness dependencies report `ready`.
- Owner console UI serves a page.
- Owner login returns an access token.
- Dashboard KPIs and integration status are readable with the token.
- A one-time WebSocket ticket can be minted.
- An approval-gated `send_email` tool creates an approval request.
- The approval appears in the queue, can be approved, and can be replayed once.
- The approved email execution records a communication log.

## Local Run

```bash
COMPOSE_SMOKE_CLEANUP=1 ./scripts/compose-smoke.sh
```

Useful options:

- `COMPOSE_SMOKE_BUILD=0` skips image rebuilds.
- `COMPOSE_SMOKE_SKIP_UP=1` runs checks against an already-running stack.
- `COMPOSE_SMOKE_CLEANUP=1` tears the stack down after the run.
- `API_BASE` and `UI_BASE` override the default `http://localhost:8000` and
  `http://localhost:3001`.

If `.env` is missing, the script temporarily copies `.env.example` for the run.

## CI

The CI workflow runs this smoke test on `workflow_dispatch` and the nightly schedule.
It is intentionally not part of every pull request because it builds and starts the
Docker Compose stack.

## Troubleshooting

- If API health or readiness times out, inspect `docker compose logs core postgres redis
  qdrant temporal opa`.
- If login fails, verify `OWNER_EMAIL` and `OWNER_PASSWORD` match the Compose
  environment.
- If approval replay fails, inspect `docker compose logs core` and the latest
  communication log rows.
- If ports are occupied, override published ports in a local Compose override or stop
  the conflicting local services.
