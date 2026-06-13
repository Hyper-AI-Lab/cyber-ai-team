# ERPNext Business Operations Completion Progress

## 2026-06-08T01:31:06Z - STEP-000 - Baseline inventory

- Files/services changed: `docs/progress/erpnext-business-ops-completion.md` created.
- Commands run:
  - `sed -n '1,420p' docker-compose.yml`
  - `sed -n '1,260p' backend/src/cyber_team/integrations/erpnext.py`
  - `sed -n '1,260p' backend/src/cyber_team/api/routes/integrations.py`
  - `sed -n '130,245p' backend/src/cyber_team/api/routes/operations.py`
  - `sed -n '1,220p' frontend/src/components/IntegrationsView.tsx`
  - `sed -n '250,410p' frontend/src/components/OperationsView.tsx`
  - `sed -n '1,260p' backend/src/cyber_team/tools/registry.py`
- Result:
  - Confirmed the current Compose ERP profile is a single `frappe/erpnext:latest` service and does not include the full Frappe runtime/site bootstrap stack.
  - Confirmed the current ERPNext client supports basic read/write methods but lacks validation/status and several write methods required by CRM/tasks/tickets/procurement.
  - Confirmed `crm_contact_update`, `crm_deal_update`, `task_create`, `task_update`, `ticket_create`, `ticket_update`, `procurement_request`, and `ci_trigger` are currently side-effect blockers or unavailable placeholders.
  - Confirmed readiness currently treats all non-live communication providers as blockers when live executors are required.
- Evidence path/link:
  - `docker-compose.yml`
  - `backend/src/cyber_team/integrations/erpnext.py`
  - `backend/src/cyber_team/tools/registry.py`
  - `backend/src/cyber_team/api/routes/integrations.py`
  - `backend/src/cyber_team/api/routes/operations.py`
  - `frontend/src/components/IntegrationsView.tsx`
  - `frontend/src/components/OperationsView.tsx`
- Next step:
  - Replace the ERP profile with a production-shaped Frappe/ERPNext Docker stack and add environment keys.

## 2026-06-08T01:36:01Z - STEP-001 - ERPNext compose profile and environment surface

- Files/services changed:
  - `docker-compose.yml`
  - `.env.example`
  - `deploy/environments/staging.env.example`
  - `deploy/environments/production.env.example`
- Commands run:
  - `docker compose --profile erp config >/tmp/cyber-team-erp-compose-config.yml`
- Result:
  - Replaced the in-repository ERPNext Compose definition with a full `erp` profile using pinned `frappe/erpnext:v16.21.1`, MariaDB 10.11, Redis cache, Redis queue, backend, frontend, websocket, scheduler, short/long workers, configurator, and idempotent create-site services.
  - Added persistent ERPNext volumes for sites, logs, MariaDB data, Redis cache data, and Redis queue data.
  - Pointed Cyber-Team core/worker at the ERPNext frontend service inside Compose.
  - Added ERPNext, required-provider, and GitHub CI execution environment keys to committed env examples.
  - Verified the Compose model renders successfully for `--profile erp`.
- Evidence path/link:
  - `/tmp/cyber-team-erp-compose-config.yml`
  - `docker-compose.yml`
  - `.env.example`
  - `deploy/environments/staging.env.example`
  - `deploy/environments/production.env.example`
- Next step:
  - Add ERPNext bootstrap automation and operational documentation for Caddy, validation, backup, restore, and rollback.

## 2026-06-08T01:39:02Z - STEP-002 - ERPNext bootstrap and operations runbook

- Files/services changed:
  - `scripts/bootstrap-erpnext.sh`
  - `docs/runbooks/erpnext.md`
- Commands run:
  - `bash -n scripts/bootstrap-erpnext.sh`
  - `docker compose --profile erp config >/tmp/cyber-team-erp-compose-config.yml`
- Result:
  - Added an idempotent ERPNext bootstrap script that starts the ERP profile, ensures required staging env keys, creates/validates the ERPNext site, creates or updates the Cyber-Team integration user, generates Frappe API credentials, validates token REST access, and writes non-secret evidence under `dist/erpnext/bootstrap/`.
  - Added the ERPNext runbook covering Compose validation, bootstrap, Caddy basic-auth reverse proxy configuration, smoke validation, backup, restore drill evidence, and rollback.
  - Verified the bootstrap script shell syntax and re-validated the ERP Compose profile.
- Evidence path/link:
  - `scripts/bootstrap-erpnext.sh`
  - `docs/runbooks/erpnext.md`
  - `/tmp/cyber-team-erp-compose-config.yml`
- Next step:
  - Extend the Cyber-Team ERPNext client and replace in-scope placeholder business tools with real ERPNext-backed executors.

## 2026-06-08T01:42:58Z - STEP-003 - ERPNext client and business tool executors

- Files/services changed:
  - `backend/src/cyber_team/config.py`
  - `backend/src/cyber_team/integrations/erpnext.py`
  - `backend/src/cyber_team/tools/registry.py`
- Commands run:
  - `PYTHONPATH=backend/src python3 -m compileall -q backend/src/cyber_team/config.py backend/src/cyber_team/integrations/erpnext.py backend/src/cyber_team/tools/registry.py`
- Result:
  - Added ERPNext site/edge/integration user settings, required provider configuration, and GitHub workflow dispatch settings.
  - Extended the ERPNext client with validation, integration status, generic document list/create/update, URL-safe resource paths, and real methods for Contact, Opportunity, Task, Issue, Material Request, Supplier, Customer, Project, Sales Invoice, Lead, and generic read/update.
  - Replaced placeholder registrations for CRM contact/deal updates, task create/update, ticket create/update, and procurement request with approval-gated ERPNext-backed side-effect tools.
  - Added payload validation so malformed tasks, issues, contact/deal updates, and material requests fail clearly instead of reporting advisory/prepared success.
  - Verified focused backend compilation.
- Evidence path/link:
  - `backend/src/cyber_team/config.py`
  - `backend/src/cyber_team/integrations/erpnext.py`
  - `backend/src/cyber_team/tools/registry.py`
- Next step:
  - Update integration and operations readiness APIs, then surface ERPNext/required-provider state in the owner console.

## 2026-06-08T01:47:00Z - STEP-004 - Integration and readiness API/UI surfaces

- Files/services changed:
  - `backend/src/cyber_team/api/routes/integrations.py`
  - `backend/src/cyber_team/api/routes/operations.py`
  - `frontend/src/components/IntegrationsView.tsx`
  - `frontend/src/components/OperationsView.tsx`
- Commands run:
  - `PYTHONPATH=backend/src python3 -m compileall -q backend/src/cyber_team/api/routes/integrations.py backend/src/cyber_team/api/routes/operations.py`
- Result:
  - Added ERPNext status and ERPNext validation support to `/api/integrations/status` and `/api/integrations/validate`.
  - Added required-provider classification so SMTP, IMAP, and ERPNext can block staging/proof readiness while disabled optional providers are reported as optional disabled.
  - Updated `/api/operations/readiness` with ERPNext status, required provider names, required blockers, and optional disabled providers.
  - Updated the owner console integrations and operations views to show ERPNext health, required provider blockers, and optional disabled provider state.
  - Verified focused backend route compilation.
- Evidence path/link:
  - `backend/src/cyber_team/api/routes/integrations.py`
  - `backend/src/cyber_team/api/routes/operations.py`
  - `frontend/src/components/IntegrationsView.tsx`
  - `frontend/src/components/OperationsView.tsx`
- Next step:
  - Add a real GitHub workflow-dispatch CI executor, readiness config, and documentation.

## 2026-06-08T01:48:45Z - STEP-005 - GitHub CI workflow-dispatch executor

- Files/services changed:
  - `backend/src/cyber_team/tools/registry.py`
  - `docs/runbooks/github-ci-trigger.md`
- Commands run:
  - `PYTHONPATH=backend/src python3 -m compileall -q backend/src/cyber_team/config.py backend/src/cyber_team/tools/registry.py`
- Result:
  - Replaced the `ci_trigger` advisory/placeholder path with a real GitHub Actions `workflow_dispatch` executor.
  - Added dynamic readiness so `ci_trigger` reports `live` only when GitHub token, repository, workflow, and ref settings are configured; otherwise it reports `configuration_required`.
  - Preserved approval-gated/manual-only behavior for CI dispatch as an external side effect.
  - Added a GitHub CI trigger runbook with required settings, readiness behavior, execution parameters, and manual smoke instructions.
  - Verified focused compilation for the registry/config path.
- Evidence path/link:
  - `backend/src/cyber_team/tools/registry.py`
  - `docs/runbooks/github-ci-trigger.md`
- Next step:
  - Add and update backend/frontend tests, then run focused quality gates.

## 2026-06-08T01:57:12Z - STEP-006 - Tests and quality gates

- Files/services changed:
  - `backend/tests/test_tools.py`
  - `backend/tests/test_erpnext_client.py`
  - `backend/tests/test_integration_routes.py`
  - `backend/tests/test_api_operations.py`
- Commands run:
  - `PYTHONPATH=backend/src .venv-quality/bin/python -m pytest backend/tests/test_tools.py backend/tests/test_erpnext_client.py backend/tests/test_integration_routes.py backend/tests/test_api_operations.py -q`
  - `PYTHONPATH=backend/src .venv-quality/bin/python -m pytest backend/tests -q`
  - `.venv-quality/bin/ruff check backend/src backend/tests backend/alembic`
  - `PYTHONPATH=backend/src .venv-quality/bin/python -m compileall -q backend/src backend/tests/test_tools.py backend/tests/test_erpnext_client.py backend/tests/test_integration_routes.py backend/tests/test_api_operations.py`
  - `docker compose --profile erp config >/tmp/cyber-team-erp-compose-config.yml`
  - `git diff --check`
  - `PYTHONPATH=src ../.venv-quality/bin/alembic upgrade head --sql >/tmp/cyber-team-alembic-upgrade-head.sql`
  - `npm test`
  - `npm run build`
  - `npm run lint`
- Result:
  - Added backend tests for ERPNext client validation/write methods, ERPNext-backed business tool readiness/execution, procurement validation, ERPNext integration validation, optional-disabled provider readiness, and GitHub CI trigger readiness.
  - Focused backend tests passed: 23 passed.
  - Full backend tests passed: 130 passed, 2 warnings.
  - Ruff passed for backend source, tests, and Alembic.
  - Backend compile, Compose ERP profile config, whitespace checks, Alembic offline SQL rendering, Next production build, and Next lint all passed.
  - Frontend Vitest did not execute because the container has Node v18.19.1 and the installed Vitest/Rolldown dependency imports `node:util.styleText`, which this Node version does not export.
- Evidence path/link:
  - `backend/tests/test_tools.py`
  - `backend/tests/test_erpnext_client.py`
  - `backend/tests/test_integration_routes.py`
  - `backend/tests/test_api_operations.py`
  - `/tmp/cyber-team-erp-compose-config.yml`
  - `/tmp/cyber-team-alembic-upgrade-head.sql`
- Next step:
  - Configure staging environment/Caddy for ERPNext, bootstrap the live ERPNext stack, and run staging smoke validation.

## 2026-06-08T02:18:00Z - STEP-007 - ERPNext staging bootstrap attempt

- Files/services changed:
  - `deploy/environments/staging.env` (ignored local secrets/config only)
  - `cyberteam-staging-erpnext-*` Docker services and volumes
  - `docker-compose.yml`
- Commands run:
  - `ERPNEXT_ENV_FILE=deploy/environments/staging.env ./scripts/bootstrap-erpnext.sh`
  - `docker compose --env-file deploy/environments/staging.env --profile erp ps`
  - `curl -fsS -H "Host: erpnext.hyperailab.com" http://127.0.0.1:18100/api/method/ping`
  - `docker compose --env-file deploy/environments/staging.env --profile erp exec -T erpnext-backend bench --site erpnext.hyperailab.com execute frappe.db.exists --args '["User", "cyberteam.integration@example.local"]'`
- Result:
  - First bootstrap run exposed a Compose command formatting issue in `erpnext-create-site`; the ERPNext site command was corrected to a single-line shell command.
  - ERPNext containers and ERP-only volumes were recreated, the site install completed, and ERPNext backend/frontend/websocket/scheduler/queue services started.
  - ERPNext ping passed on the published local frontend port with the configured Host header.
  - The Cyber-Team integration user exists in ERPNext and has an API key, but the first bootstrap wrapper exited before writing final token evidence.
  - A manual masked diagnostic confirmed ERPNext `generate_keys` returns `api_key` and `api_secret`; the next run will validate bootstrap idempotency and write the current secret pair to the ignored staging env.
- Evidence path/link:
  - `docker-compose.yml`
  - `scripts/bootstrap-erpnext.sh`
  - `http://127.0.0.1:18100/api/method/ping`
- Next step:
  - Rerun `scripts/bootstrap-erpnext.sh` idempotently, verify token validation, then configure Caddy for `erpnext.hyperailab.com`.

## 2026-06-08T02:24:19Z - STEP-008 - ERPNext bootstrap idempotency fix and pass

- Files/services changed:
  - `scripts/bootstrap-erpnext.sh`
  - `deploy/environments/staging.env` (ignored local ERPNext API key/secret update)
  - `cyberteam-staging-erpnext-*` Docker services
- Commands run:
  - `bash -n scripts/bootstrap-erpnext.sh`
  - `ERPNEXT_ENV_FILE=deploy/environments/staging.env ./scripts/bootstrap-erpnext.sh`
- Result:
  - Fixed bootstrap scalar parsing so piped bench output is read correctly.
  - Fixed ERPNext token extraction so `api_secret` is read by field name instead of accidentally reusing `api_key`.
  - Added explicit Frappe DB commits after integration-user updates and token generation.
  - Re-ran the bootstrap idempotently against the existing site; runtime services were reused, token-auth REST validation passed, and API credentials were written to the ignored staging env.
- Evidence path/link:
  - `scripts/bootstrap-erpnext.sh`
  - `/home/projects/cyber-team/dist/erpnext/bootstrap/erpnext-bootstrap-20260608T022413Z.json`
- Next step:
  - Add the `erpnext.hyperailab.com` Caddy route with basic auth, validate Caddy, and run public/private ERPNext smoke checks.

## 2026-06-08T02:37:14Z - STEP-009 - Caddy route, staging deploy, and readiness smoke

- Files/services changed:
  - `/etc/caddy/Caddyfile` (host-level ERPNext route; backup created under `/etc/caddy/`)
  - `docs/runbooks/erpnext.md`
  - `backend/src/cyber_team/api/routes/operations.py`
  - `backend/tests/test_api_operations.py`
  - `cyberteam-staging-core`, `cyberteam-staging-worker`, and `cyberteam-staging-ui`
- Commands run:
  - `caddy validate --config /etc/caddy/Caddyfile`
  - `systemctl reload caddy`
  - `curl -fsS -H "Host: erpnext.hyperailab.com" http://127.0.0.1:18100/api/method/ping`
  - `docker compose --env-file deploy/environments/staging.env up -d --build core worker ui`
  - `PYTHONPATH=backend/src .venv-quality/bin/python -m pytest backend/tests/test_api_operations.py -q`
  - `docker compose --env-file deploy/environments/staging.env up -d --build core worker`
  - `COMPOSE_SMOKE_SKIP_UP=1 COMPOSE_SMOKE_ENV_FILE=deploy/environments/staging.env ./scripts/compose-smoke.sh`
- Result:
  - Added and validated a Caddy basic-auth reverse proxy block for `erpnext.hyperailab.com` to the ERPNext frontend port.
  - Caddy reloaded successfully; local HTTP routing returns a redirect to `https://erpnext.hyperailab.com/...`.
  - Public HTTPS validation is blocked until DNS for `erpnext.hyperailab.com` resolves to this host; local HTTPS/SNI currently fails before routing because Caddy cannot obtain a certificate without DNS.
  - Direct ERPNext API token validation passed with HTTP 200 against the published local frontend port.
  - Rebuilt and restarted Cyber-Team core, worker, and UI using the updated ERPNext env/config.
  - Fixed operations readiness so optional SMS/voice/messaging/GitHub CI configuration gaps are visible as non-blocking side-effect items instead of required blockers.
  - Operations readiness is now `ready` with no blockers, no side-effect blockers, no required integration blockers, and seven optional-disabled providers.
  - Public Cyber-Team compose smoke passed through health, UI, owner login, KPIs, integrations, WebSocket ticket, tool readiness, approval queue, and safe rejection of the live email side effect.
- Evidence path/link:
  - `/etc/caddy/Caddyfile`
  - `/etc/caddy/Caddyfile.cyber-team-erpnext-20260608T022551Z.bak`
  - `docs/runbooks/erpnext.md`
  - `backend/src/cyber_team/api/routes/operations.py`
  - `backend/tests/test_api_operations.py`
  - `/home/projects/cyber-team/dist/erpnext/bootstrap/erpnext-bootstrap-20260608T022413Z.json`
- Next step:
  - Run final repo quality gates, inspect git status, and identify any remaining DNS/GitHub-token-only operational follow-ups.

## 2026-06-08T02:40:16Z - STEP-010 - Final quality gates

- Files/services changed:
  - No additional services changed after STEP-009.
  - Verification covered backend, frontend, Compose, shell scripts, and live staging smoke.
- Commands run:
  - `PYTHONPATH=backend/src .venv-quality/bin/python -m pytest backend/tests -q`
  - `.venv-quality/bin/ruff check backend/src backend/tests backend/alembic`
  - `PYTHONPATH=backend/src .venv-quality/bin/python -m compileall -q backend/src backend/tests`
  - `bash -n scripts/bootstrap-erpnext.sh scripts/compose-smoke.sh`
  - `docker compose --profile erp config >/tmp/cyber-team-erp-compose-config-final.yml`
  - `git diff --check`
  - `npm run build`
  - `npm run lint`
  - `npm test`
- Result:
  - Full backend test suite passed: 130 passed, 2 warnings.
  - Ruff, backend compile, shell syntax, Compose ERP config, and diff whitespace checks passed.
  - Frontend production build and lint passed.
  - Frontend Vitest did not execute because the host Node.js runtime is v18.19.1 and the installed Rolldown/Vitest dependency imports `node:util.styleText`, which is not exported by that Node version.
- Evidence path/link:
  - `/tmp/cyber-team-erp-compose-config-final.yml`
  - `backend/tests/test_api_operations.py`
  - `docs/progress/erpnext-business-ops-completion.md`
- Next step:
  - Point DNS for `erpnext.hyperailab.com` to this host for public ERPNext TLS/browser validation, and configure GitHub dispatch credentials only if CI triggering should be part of required operational scope.

## 2026-06-08T02:47:58Z - STEP-011 - ERPNext live write smoke and integration-user permissions

- Files/services changed:
  - `scripts/bootstrap-erpnext.sh`
  - `deploy/environments/staging.env` (ignored local ERPNext token refresh)
  - ERPNext staging records: Lead, Task, and Issue smoke records
- Commands run:
  - `bash -n scripts/bootstrap-erpnext.sh`
  - `ERPNEXT_ENV_FILE=deploy/environments/staging.env ./scripts/bootstrap-erpnext.sh`
  - `docker compose --env-file deploy/environments/staging.env --profile erp exec -T erpnext-backend bench --site erpnext.hyperailab.com execute frappe.get_roles --args '["cyberteam.integration@example.local"]'`
  - ERPNext token-auth REST smoke script for Lead, Task, and Issue create/update
- Result:
  - The first live write smoke exposed that the integration user could create Leads but lacked Task create permission.
  - Replaced the unsupported Frappe `add_role` bootstrap path with an idempotent direct User document role update through `bench console`.
  - Confirmed the integration user now has Projects User, Sales User, Accounts Manager, Sales Manager, Support Team, Purchase Manager, System Manager, All, Guest, and Desk User roles.
  - Live ERPNext write smoke passed: created a staging-only Lead, Task, and Issue through token auth; updated the Lead to `Do Not Contact`, Task to `Completed`, and Issue to `Closed`.
  - Archived one earlier half-created smoke Lead to `Do Not Contact`.
- Evidence path/link:
  - `scripts/bootstrap-erpnext.sh`
  - `/home/projects/cyber-team/dist/erpnext/bootstrap/erpnext-bootstrap-20260608T024702Z.json`
  - `/home/projects/cyber-team/dist/erpnext/smoke/erpnext-live-write-smoke-20260608T024747Z.json`
- Next step:
  - Inspect final git status and report completion with DNS and optional GitHub CI credential follow-ups.

## 2026-06-11T01:42:49Z - STEP-012 - Cyber-Team approval-gated ERPNext tool smoke

- Files/services changed:
  - `scripts/erpnext-smoke.py`
  - `scripts/bootstrap-erpnext.sh`
  - `backend/src/cyber_team/tools/registry.py`
  - `backend/tests/test_tools.py`
  - `docs/runbooks/erpnext.md`
  - `cyberteam-staging-core` and `cyberteam-staging-worker`
  - ERPNext staging fixture/master records: Warehouse Type, Company, UOM, Item Group, Item
  - ERPNext staging smoke records: Lead, Task, Issue, and Material Request
- Commands run:
  - `python3 -m py_compile scripts/erpnext-smoke.py`
  - `bash -n scripts/bootstrap-erpnext.sh`
  - `ERPNEXT_ENV_FILE=deploy/environments/staging.env ./scripts/bootstrap-erpnext.sh`
  - `docker compose --env-file deploy/environments/staging.env up -d core worker`
  - `PYTHONPATH=backend/src .venv-quality/bin/python -m pytest backend/tests/test_tools.py -q`
  - `docker compose --env-file deploy/environments/staging.env up -d --build core worker`
  - `./scripts/erpnext-smoke.py --env-file deploy/environments/staging.env --api-base http://127.0.0.1:18000`
- Result:
  - Added a repeatable product-path ERPNext smoke script that logs into Cyber-Team as owner, checks ERPNext-backed tool readiness, exercises `/api/tools/execute`, approves actions through `/api/dashboard/approval/{id}/approve`, verifies target mismatch blocking, verifies consumed approval replay blocking, and writes non-secret evidence under `dist/erpnext/smoke/`.
  - The first smoke runs exposed missing ERPNext master data and permissions on the newly bootstrapped site: missing Item Group/UOM/Company/Warehouse Type/Item fixtures, missing `Item Manager` role, and stale in-container ERPNext API token after bootstrap token refresh.
  - Updated bootstrap to grant the `Item Manager` role and reran it idempotently.
  - Restarted Cyber-Team core/worker so the app consumed the refreshed ERPNext API token.
  - Added idempotent staging fixture setup for the smoke script: `Transit` Warehouse Type, `Cyber-Team Smoke Company`, `Nos` UOM, `All Item Groups`, and `CYBERTEAM-SMOKE-SERVICE` item.
  - Normalized `erpnext_create_lead` and `erpnext_invoice_create` to return the same ERPNext write-result contract used by task/ticket/procurement tools.
  - Final Cyber-Team-to-ERPNext smoke passed on the rebuilt backend: Lead, Task, Issue, and Material Request were created through approved Cyber-Team tools; Lead was archived to `Do Not Contact`, Tasks to `Completed`, Issue to `Closed`, and Material Request remains a staging Draft audit record.
- Evidence path/link:
  - `scripts/erpnext-smoke.py`
  - `scripts/bootstrap-erpnext.sh`
  - `backend/src/cyber_team/tools/registry.py`
  - `backend/tests/test_tools.py`
  - `docs/runbooks/erpnext.md`
  - `/home/projects/cyber-team/dist/erpnext/bootstrap/erpnext-bootstrap-20260611T013625Z.json`
  - `/home/projects/cyber-team/dist/erpnext/smoke/cyberteam-erpnext-tool-smoke-20260611T014226Z.json`
- Next step:
  - Run full quality gates, then stage/commit/push the tracked implementation changes if verification stays green.

## 2026-06-11T01:47:14Z - STEP-013 - Final verification for Cyber-Team ERPNext smoke layer

- Files/services changed:
  - No additional service changes after STEP-012.
  - Verification covered backend, frontend, shell scripts, Compose config, Cyber-Team compose smoke, and Cyber-Team-to-ERPNext tool smoke.
- Commands run:
  - `PYTHONPATH=backend/src .venv-quality/bin/python -m pytest backend/tests -q`
  - `.venv-quality/bin/ruff check backend/src backend/tests backend/alembic scripts/erpnext-smoke.py`
  - `PYTHONPATH=backend/src .venv-quality/bin/python -m compileall -q backend/src backend/tests`
  - `python3 -m py_compile scripts/erpnext-smoke.py`
  - `bash -n scripts/bootstrap-erpnext.sh scripts/compose-smoke.sh`
  - `docker compose --profile erp config >/tmp/cyber-team-erp-compose-config-final-20260611.yml`
  - `git diff --check`
  - `COMPOSE_SMOKE_SKIP_UP=1 COMPOSE_SMOKE_ENV_FILE=deploy/environments/staging.env API_BASE=http://127.0.0.1:18000 UI_BASE=http://127.0.0.1:13001 ./scripts/compose-smoke.sh`
  - `npm run build`
  - `npm run lint`
  - `npm test`
- Result:
  - Full backend test suite passed: 131 passed, 2 warnings.
  - Ruff passed for backend source, backend tests, Alembic, and the new ERPNext smoke script.
  - Backend compile, smoke script compile, shell syntax, Compose ERP config, diff whitespace check, Cyber-Team compose smoke, frontend production build, and frontend lint all passed.
  - Frontend Vitest still did not execute because the host Node.js runtime is v18.19.1 and the installed Rolldown/Vitest dependency imports `node:util.styleText`, which is not exported by that Node version.
- Evidence path/link:
  - `/tmp/cyber-team-erp-compose-config-final-20260611.yml`
  - `/home/projects/cyber-team/dist/erpnext/smoke/cyberteam-erpnext-tool-smoke-20260611T014226Z.json`
  - `scripts/erpnext-smoke.py`
- Next step:
  - Stage, commit, and push the tracked implementation changes to GitHub.

## 2026-06-11T02:27:31Z - STEP-014 - ERPNext backup and restore drill automation

- Files/services changed:
  - `scripts/erpnext-backup.sh`
  - `scripts/erpnext-restore-drill.sh`
  - `docs/runbooks/erpnext.md`
  - `docs/runbooks/backup-restore.md`
  - ERPNext temporary restore drill sites: `restore-drill-20260611T015920Z.local` and `restore-drill-20260611T021559Z.local`
- Commands run:
  - `bash -n scripts/erpnext-backup.sh`
  - `ERPNEXT_ENV_FILE=deploy/environments/staging.env ./scripts/erpnext-backup.sh`
  - `ERPNEXT_ENV_FILE=deploy/environments/staging.env ERPNEXT_RESTORE_BACKUP_MANIFEST=backups/erpnext/staging/20260611T015857Z/backup-manifest.json ./scripts/erpnext-restore-drill.sh`
  - `docker compose --env-file deploy/environments/staging.env --profile erp exec -T erpnext-backend bash -lc 'test ! -d sites/restore-drill-20260611T021559Z.local && echo temporary-site-cleaned || echo temporary-site-still-present'`
- Result:
  - Added an ERPNext backup script that runs `bench backup --with-files` inside the ERPNext backend container, copies database/public/private file artifacts into ignored local backup storage, records file sizes and SHA-256 checksums, and writes non-secret evidence.
  - Added an ERPNext restore drill script that creates a temporary ERPNext site, restores the selected database and file backup, runs `bench migrate`, validates API reachability through the ERPNext frontend, records restored DocType row counts and integration-user presence, writes evidence, and drops the temporary site during cleanup.
  - Verified a staging ERPNext backup artifact at `backups/erpnext/staging/20260611T015857Z/backup-manifest.json`.
  - Verified two restore drill passes. The final pass restored Lead, Task, Issue, Material Request, Item, Company, and User records, validated `{"message":"pong"}`, confirmed `cyberteam.integration@example.local`, and cleaned up `restore-drill-20260611T021559Z.local`.
  - Updated runbooks so ERPNext backup/restore uses the automated scripts and so the core backup/restore runbook points to the ERPNext-specific flow.
- Evidence path/link:
  - `/home/projects/cyber-team/backups/erpnext/staging/20260611T015857Z/backup-manifest.json`
  - `/home/projects/cyber-team/dist/erpnext/backups/erpnext-backup-20260611T015857Z.json`
  - `/home/projects/cyber-team/dist/erpnext/restore-drills/erpnext-restore-drill-20260611T021559Z.json`
  - `docs/runbooks/erpnext.md`
  - `docs/runbooks/backup-restore.md`
- Next step:
  - Run final quality gates for the backup/restore automation change, then stage, commit, push, and watch GitHub CI.

## 2026-06-11T02:29:25Z - STEP-015 - Verification for ERPNext backup/restore automation

- Files/services changed:
  - No additional implementation changes after STEP-014.
  - Verification touched staging Cyber-Team approval records and staging ERPNext smoke records through existing smoke scripts.
- Commands run:
  - `bash -n scripts/erpnext-backup.sh scripts/erpnext-restore-drill.sh scripts/bootstrap-erpnext.sh scripts/compose-smoke.sh`
  - `docker compose --env-file deploy/environments/staging.env --profile erp config >/tmp/cyber-team-erp-compose-config-restore-drill-20260611.yml`
  - `git diff --check`
  - `PYTHONPATH=backend/src .venv-quality/bin/python -m pytest backend/tests -q`
  - `.venv-quality/bin/ruff check backend/src backend/tests backend/alembic scripts/erpnext-smoke.py`
  - `PYTHONPATH=backend/src .venv-quality/bin/python -m compileall -q backend/src backend/tests`
  - `COMPOSE_SMOKE_SKIP_UP=1 COMPOSE_SMOKE_ENV_FILE=deploy/environments/staging.env API_BASE=http://127.0.0.1:18000 UI_BASE=http://127.0.0.1:13001 ./scripts/compose-smoke.sh`
  - `./scripts/erpnext-smoke.py --env-file deploy/environments/staging.env --api-base http://127.0.0.1:18000`
- Result:
  - Shell syntax checks, ERPNext Compose config, diff whitespace check, Ruff, and backend compile all passed.
  - Backend test suite passed: 131 passed, 2 warnings.
  - Cyber-Team compose smoke passed against `https://cyberteam.hyperailab.com`, including owner login, dashboard KPIs, integration status, one-time WebSocket ticket minting, tool readiness, and approval queue rejection flow.
  - Cyber-Team-to-ERPNext smoke passed through approval-gated tool execution: Lead, Task, Issue, and Material Request were created in ERPNext; Lead was archived to `Do Not Contact`, Tasks to `Completed`, Issue to `Closed`, and Material Request remains a staging Draft audit record.
- Evidence path/link:
  - `/tmp/cyber-team-erp-compose-config-restore-drill-20260611.yml`
  - `/home/projects/cyber-team/dist/erpnext/restore-drills/erpnext-restore-drill-20260611T021559Z.json`
  - `/home/projects/cyber-team/dist/erpnext/smoke/cyberteam-erpnext-tool-smoke-20260611T022918Z.json`
- Next step:
  - Stage, commit, and push the tracked backup/restore automation changes to GitHub, then watch CI.

## 2026-06-13T07:12:15Z - STEP-016 - Public ERPNext DNS, TLS, and edge-auth validation

- Files/services changed:
  - `/etc/caddy/Caddyfile`
  - `docs/runbooks/erpnext.md`
  - Caddy service reloaded after DNS for `erpnext.hyperailab.com` resolved to this host.
- Commands run:
  - `getent hosts erpnext.hyperailab.com`
  - `caddy validate --config /etc/caddy/Caddyfile`
  - `systemctl reload caddy`
  - `journalctl -u caddy --since '2 minutes ago' --no-pager`
  - `curl -I --max-time 20 https://erpnext.hyperailab.com/login`
  - `curl -sS -o /dev/null -w '%{http_code}\n' --max-time 20 https://erpnext.hyperailab.com/login`
  - `curl -sS -o /tmp/erpnext-login-check.html -w '%{http_code}\n' --max-time 25 -u '<edge-basic-auth>' https://erpnext.hyperailab.com/login`
  - `./scripts/erpnext-smoke.py --env-file deploy/environments/staging.env --api-base http://127.0.0.1:18000`
  - `COMPOSE_SMOKE_SKIP_UP=1 COMPOSE_SMOKE_ENV_FILE=deploy/environments/staging.env API_BASE=http://127.0.0.1:18000 UI_BASE=http://127.0.0.1:13001 ./scripts/compose-smoke.sh`
  - `git diff --check`
- Result:
  - DNS for `erpnext.hyperailab.com` resolves to this host and Caddy obtained a public Let's Encrypt certificate after reload.
  - Public ERPNext edge behavior is correct: unauthenticated requests return Caddy `401`, authenticated Caddy requests reach ERPNext and return the `Login` page with HTTP `200`.
  - Fixed the Caddy-to-ERPNext auth-header collision by stripping the consumed edge `Authorization` header before proxying to ERPNext; otherwise ERPNext interpreted the Caddy basic-auth header as ERPNext API auth and returned `401`.
  - Cyber-Team-to-ERPNext smoke passed through approval-gated Lead, Task, Issue, and Material Request tool execution, including approval target mismatch and consumed-approval checks.
  - Cyber-Team compose smoke passed after the Caddy reload.
- Evidence path/link:
  - `/home/projects/cyber-team/dist/erpnext/smoke/cyberteam-erpnext-tool-smoke-20260613T071149Z.json`
  - `docs/runbooks/erpnext.md`
  - `journalctl -u caddy` certificate issuance entries for `erpnext.hyperailab.com` at `2026-06-13T07:09:31Z`.
- Next step:
  - Stage, commit, push, and watch GitHub CI for the ERPNext public-edge runbook/progress update.
