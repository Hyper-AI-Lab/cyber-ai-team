# Cyber-Team Production Readiness Plan

This plan tracks the remaining work needed to move Cyber-Team from a verified
development system toward production-grade operation.

## Current Baseline

Confirmed as of this pass:

- Backend Ruff lint passes for `src`, `tests`, and `alembic`.
- Backend tests pass.
- Frontend TypeScript check passes.
- Frontend API/session smoke tests pass.
- Backend and frontend dependency audits pass.
- High-confidence secret scan passes.
- Docker Compose configuration validates.
- Alembic baseline migration exists and was validated against disposable PostgreSQL.
- Core remediation fixes are in place for approvals, WebSocket auth, login/session UX,
  bounded LLM history, Qdrant fallback, and workflow tool execution.
- WebSocket chat authentication uses short-lived one-time tickets for browser clients
  instead of long-lived access tokens in query strings.
- Login, token refresh, WebSocket ticket minting, chat, tool execution, and approval
  decisions have configurable in-memory per-minute rate limits.
- Production startup requires `OWNER_PASSWORD_HASH` and rejects wildcard CORS.

The system is not yet production-grade. The remaining plan is intentionally larger
than a feature checklist because it covers verification, operations, security,
data safety, and release discipline.

## Execution Status

Completed in the current implementation pass:

- Phase 1 local/CI quality gates now include backend lint/tests/compile, Alembic SQL,
  backend dependency audit, frontend typecheck/tests/audit/build, Docker Compose config,
  high-confidence secret scanning, and diff hygiene.
- Phase 2 backend coverage now includes auth/security, approval locking, tool approval
  replay, workflow resume behavior, migration SQL contracts, and memory fallback/delete
  behavior.
- Phase 2 frontend smoke coverage now verifies API client login token storage,
  refresh-and-retry behavior, token clearing after refresh failure, and WebSocket ticket
  URL generation.
- Phase 3 security hardening now includes short-lived WebSocket tickets, rate limits,
  production password-hash enforcement, dependency audit gates, and secret scanning.
- A Docker Compose smoke/e2e runner now validates API health, UI serving, owner login,
  dashboard KPIs, WebSocket ticket minting, approval-gated tool execution, approval
  replay, and communication log side effects.
- A disposable migration rehearsal script now validates Alembic upgrade behavior against
  a pre-Alembic approval schema.
- PostgreSQL/Qdrant backup and restore runbooks are documented.
- Runtime communication status is now exposed through the API and console, SMTP email
  sending is implemented, and production startup rejects simulated communications.
- Liveness and readiness endpoints now distinguish process health from dependency
  readiness for PostgreSQL, Redis, Qdrant, Temporal, and OPA.
- Outbound communications now support durable idempotency keys, Jasmin SMS, Slack
  webhooks, Telegram Bot API, and Twilio WhatsApp provider adapters.
- Outbound communication providers now have per-provider circuit breakers, delivery
  metrics, and circuit state in the Integrations view.
- Asterisk ARI can originate runtime voice calls into a configured Stasis app when
  enabled.
- Prometheus alert rules and a provisioned Grafana operations dashboard are included
  in the observability profile.
- Release candidate and rollback scripts are documented in the release runbook.

Still pending:

- Scheduled/staging execution of release checks, Compose smoke, and alert tests.
- Migration rehearsal against a representative production-like data volume.
- Formal retention/data deletion policies.
- Image scanning and staging deployment promotion flow.
- Full Asterisk media/TTS workflow beyond ARI call origination.

## Phase 1: Repeatable Quality Gates

Goal: every change can be verified the same way locally and in CI.

- Add local quality gate script.
- Add CI workflow for backend lint, tests, Alembic SQL generation, frontend typecheck,
  frontend production build, Docker Compose validation, and diff hygiene.
- Keep backend dependencies installed from the same `requirements.txt` path used by
  the Docker image.
- Add CI status as a required branch protection check once hosted.
- Record known warnings and decide whether to fail on warnings later.

Exit criteria:

- `scripts/quality-gate.sh` passes from a clean dependency environment.
- CI passes on pull requests.
- CI can run without secrets.

## Phase 2: Test Coverage Expansion

Goal: cover the most failure-prone business and integration paths.

- [x] Add API tests for auth login/refresh, protected route failures, 403 authorization
  denials, and WebSocket auth failures.
- [x] Add workflow tests for approval wait/resume/reject, tool approval replay, and failure
  persistence.
- [x] Add database tests for Alembic upgrade, idempotent upgrade, downgrade policy, indexes,
  and default/nullability constraints.
- [x] Add memory tests for Qdrant fallback, PostgreSQL recall, delete behavior, and namespace
  access rules.
- [x] Add frontend tests for login/logout, token refresh, route rendering, approval actions,
  and chat error states.
- [x] Add e2e smoke test against Docker Compose for API health, login, dashboard, and one
  approval workflow.

Exit criteria:

- High-risk backend paths have regression tests.
- Frontend has at least smoke-level UI coverage.
- Compose smoke test runs in CI or a scheduled staging job.

## Phase 3: Security Hardening

Goal: reduce exposed attack surface and remove weak production defaults.

- Replace query-string WebSocket tokens with a safer browser-compatible session or
  short-lived WebSocket ticket flow.
- Add rate limits for login, token refresh, chat, tool execution, and approval endpoints.
- Enforce production CORS allowlist and fail fast on wildcard origins.
- Add structured secret scanning in CI.
- Hash owner passwords by default and document rotation.
- Review audit logs for accidental secret/token payloads.
- Validate tool input and file paths across all side-effectful tools.
- Add dependency vulnerability scanning.
- Document OPA/OpenFGA policy ownership and deployment flow.

Exit criteria:

- No default credentials/secrets can reach production startup.
- Token handling and logs pass security review.
- CI blocks known high/critical dependency issues or has documented exceptions.

## Phase 4: Data Safety and Migration Discipline

Goal: migrations and data changes are reversible or rehearsed.

- [ ] Add staging migration rehearsal against a copy or synthetic representative dataset.
- [x] Add migration tests for existing pre-Alembic schemas.
- [x] Add backup and restore runbook for PostgreSQL and Qdrant.
- Add retention policy for memory, audit, communications, and workflow run data.
- Add data export/delete policy for customer/person-related records.
- Add seed data scripts for local/staging smoke tests.

Exit criteria:

- A migration can be rehearsed and rolled back or forward safely.
- Restore from backup is tested and documented.

## Phase 5: Reliability and Operations

Goal: operators can detect, diagnose, and recover from failures.

- Add health checks that include database, Temporal, Qdrant, and critical integrations.
- Add readiness endpoints distinct from liveness.
- Add request IDs and structured logs.
- [x] Add retry/timeouts/circuit breaker policy for outbound communication providers.
- [x] Add initial timeout/retry policy for outbound communication providers.
- [x] Add idempotency keys for outbound communication tool execution.
- [x] Add initial Prometheus metrics for communication deliveries and provider
  circuit state.
- Add Prometheus metrics for approvals, tool executions, LLM failures, workflow states,
  queue delays, and auth failures.
- [x] Add starter Grafana dashboard and Prometheus alert rules for API,
  communication, circuit breaker, authorization, and audit signals.
- Build deeper Grafana dashboards and alert rules.
- Document runbooks for degraded Qdrant, Temporal outage, failed migrations, and LLM
  provider failure.

Exit criteria:

- On-call can answer "is it up?", "what is broken?", and "what do I do next?" without
  reading source code.

## Phase 6: Deployment and Release Process

Goal: releases are repeatable, observable, and recoverable.

- Pin dependency versions with generated lockfiles for backend and frontend.
- Build and scan Docker images in CI.
- Add environment-specific compose or deployment manifests.
- [x] Add release checklist and rollback checklist.
- Add staging environment promotion flow.
- Add versioning/build metadata surfaced in API health and UI.
- Add smoke tests after deployment.

Exit criteria:

- A release can be built, deployed to staging, promoted, and rolled back by following
  documented steps.

## Phase 7: Product and Workflow Completeness

Goal: core workflows behave predictably for real users.

- Define user-visible states for agents, workflows, approvals, tools, and integrations.
- Add validation and UX for empty/loading/error states across the console.
- Add permission-aware UI behavior.
- Add import/export for workflows and role manifests.
- Add admin views for integration status and failed background work.
- Add workflow templates and example seed data for demos/staging.

Exit criteria:

- A new operator can set up a company, create agents, run an approval workflow, inspect
  failures, and recover from common issues through supported UI/API paths.

## Phase 8: Documentation and Onboarding

Goal: senior engineers and operators can onboard quickly.

- Keep architecture and execution-flow docs current.
- Add API usage examples.
- Add local development, CI, staging, and production runbooks.
- Document environment variables and secret requirements.
- Document testing strategy and how to add tests.
- Document known limitations and unsupported production scenarios.

Exit criteria:

- Documentation is sufficient to operate and extend the system without relying on chat
  history.

## Execution Order

1. Phase 1 quality gates.
2. Backend tests for auth, approvals, workflows, memory, and migrations.
3. Compose smoke/e2e test.
4. Security hardening for tokens, rate limits, secrets, and dependency scans.
5. Operational health/readiness and observability.
6. Release/deployment discipline.
7. Product hardening and docs.
