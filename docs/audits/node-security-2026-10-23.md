# Node Security Maintenance - 2026-10-23

## Scope and Result

Owner-authorized staging security maintenance is deployed and read back from the
running processes. This is not closure of the v5 milestone or production promotion.
The host's existing Node 24.22.1 needed no replacement.

| Component | Before | Verified after |
| --- | --- | --- |
| Owner Console builder, server and CI | Node 20 / live 20.20.2 | Node 24.22.1 |
| Six ERPNext application containers | Node 24.12.0 | Node 24.22.1 |
| Desktop-connected Jev MCP process | Node 22.23.3 | Node 22.23.4 |
| Frontend Next.js | 15.5.25 | 15.5.28 |
| Frontend Sharp | 0.35.4 | 0.35.5 |
| Frontend source-map-js lock | 1.2.1 | 1.2.2 |

The [official Node security bulletin](https://nodejs.org/en/blog/vulnerability/october-2026-security-releases)
identifies the patched maintained versions. Updating host Node alone does not
update containers or an already-running isolated MCP process. Exploit reachability
and compromise were not tested; this work does not establish that exploitation occurred.

Frontend runtime patches also address the reviewed
[Next.js advisory](https://github.com/vercel/next.js/security/advisories/GHSA-2464-fhr4-w9xw),
[Sharp advisory](https://github.com/lovell/sharp/security/advisories/GHSA-wq5f-xc86-pv6w)
and [source-map-js release](https://github.com/7rulnik/source-map-js/releases/tag/v1.2.2).
Do not infer that every advisory's exploit prerequisites were present on this host.

## Artifacts and Isolation

- UI: `cyber-team-ui:0.4.14-node24.22.1-r2`, local image ID
  `sha256:388ac18d0a466fb12bfb85daf4552fe0502db133a808a96273690f628e193fef`.
- ERPNext: `cyber-team-erpnext:16.21.1-node24.22.1`, local image ID
  `sha256:194db9cf228f831cdc401975ce60f1fe6c598148de9246e4c4725fe8756d1c7d`.
  The derivative replaces only the Node executable in the pinned ERPNext image.
  Its existing NVM directory still contains `v24.12.0` in the path; executable
  inspection, not the directory name, establishes Node 24.22.1.
- Jev: the official 22.23.4 archive SHA-256 was verified as
  `1805bcd35e6815559bd2eb97db438d3fdb0eb3a5e6a977b7c1badca76337e9ce`.
  The existing sealed installer changed the MCP executable only. Release, hooks,
  guidance, selected models and budget configuration were preserved. The actual
  desktop MCP connection was verified after reconnection, not just a test client.
- The unchanged core/worker image remains `0.4.15-vision-v5.6`. Schema remains
  `0026_immutable_audit_archives`; both company-cycle and governor schedules are
  paused. No migration, company write, email canary or inference activation ran.
- UI and ERPNext host bindings remain localhost-only. UI public HTTPS delivery
  was also checked through the existing reverse proxy.

Image IDs here identify local build artifacts, not a published registry manifest
or a full image-vulnerability attestation. Application version unification remains
separate v5 work; the UI retains its 0.4.14 application version with a maintenance
build label, while core remains 0.4.15.

## Verification

- Fresh ERPNext database, files and configuration backup; isolated restoration
  into the exact candidate image passed eight business-count comparisons and an
  Administrator functional read. Public/private attachment archives were both
  empty and matched. All owned restore resources were removed.
- Frontend: 34 tests, TypeScript, lint and standalone production build passed.
- Runtime checks: UI root HTTP 200, all eight referenced assets served, Sharp
  generates a PNG; repeated checks passed locally and via public HTTPS. The app's
  login/dashboard are states inside `/`, not separate HTTP routes.
- ERPNext: all six application containers report Node 24.22.1; ping, login and
  Socket.IO polling returned HTTP 200 after restart and again twelve hours later.
- Jev: 18 isolated installer tests and a separate MCP smoke passed; the connected
  process subsequently reported Node 22.23.4 and the unchanged verified release.
- Eight focused runtime-pin/resource-policy tests, Ruff, Compose validation,
  secret-pattern scan, GCP isolation policy, resource policy and diff checks pass.
  The resource scan now includes the new ERPNext derivative Dockerfile; an
  unreviewed stage is rejected by a regression test. Existing generic Compose
  `latest` defaults still generate warnings; staging uses pinned local images.
- `npm audit --omit=dev --audit-level=moderate`: zero reported vulnerabilities.
  A fresh full audit at about 16:43 UTC reports eight development-only package
  findings (four high, four moderate), versus fifteen in the earlier registry
  response. This is time-specific advisory coverage, not evidence of additional
  patches. Affected packages include Vitest/mocker, Vite, brace-expansion,
  browserslist, baseline-browser-mapping, js-yaml and postcss-selector-parser.

Earlier `npm audit fix` and a targeted update failed inside npm with a null
`edgesOut` error. No forced update or framework-major migration was used. The
development findings remain open for the v5 quality gate; do not expose dev/test
servers to untrusted clients or process untrusted build input as mitigation.
No authenticated browser E2E, full image scan, full v5 suite or new soak is claimed.

## Rollback and Restart Safety

Private backup and configuration rollback files are retained under
`backups/erpnext/node-security-20261023/20261023T035324Z-3117666/`.
Isolated restore evidence is under `dist/node-security-20261023/erpnext-restore/`.
Never publish these directories or copy their credential-bearing configuration.

The ignored staging environment pins the patched UI and ERPNext images. A new
maintenance record under `dist/promotions/staging/` preserves the actual unchanged
core plus the patched UI, preventing `scripts/start-staging-current.sh` from
selecting the obsolete 0.4.14 core/UI promotion. Its dry run was verified; the
full-stack startup command was not executed. Component-specific versions and the
restricted acceptance scope are explicit in that record.

Emergency rollback images are retained locally:

- UI: `sha256:e3f293896a9192ad58ea90fa41e7aa445dd880beb8b860612fc22a6ec764ba62`.
- ERPNext: `sha256:e1479ded7bfd78cd76015e62da703311a50c16ecca0ef381e851cfa52f0aab16`.

Rollback requires an explicit operator decision because it restores vulnerable
runtimes. Update only the affected image override and maintenance record, then
recreate only the affected services with `--no-deps --no-build --pull never`.
Preserve correct public URLs/build metadata and paused schedules. Do not run a
database restore or migration for this binary-only rollback. Recheck actual Node
versions, UI assets and ERPNext connectivity; plan immediate forward remediation.
Jev's prior toolchain remains available, but any rollback must use the same sealed
installer and preserve owner configuration rather than editing trust records.

## Credential Handling Follow-up

An earlier Docker inspection mistakenly emitted application environment values
into the private tool session. The owner was notified; affected credentials should
be rotated. Rotation has not been performed and no compromise is asserted.
Values were not copied into this report or repository. Subsequent inspections
filter allowlisted fields before output. A passing repository secret scan does
not remove that separate private-session exposure.

## Remote Verification Follow-up

Security checkpoint `cbbfa4d` was pushed to `codex/vision-integrity-v5`; no merge
to main occurred. [Manual CI run 39990335294](https://github.com/Hyper-AI-Lab/cyber-ai-team/actions/runs/39990335294)
passed Frontend, Compose/Secrets/Diff, Observability Config and Docker Image Scan.
Backend stopped at import time because fresh SQLAlchemy 2.1 installs omitted
greenlet; Compose smoke also failed on an unhealthy freshly built core. Its
initial logs did not independently establish the cause. Live staging was untouched.

The requirements now explicitly request `sqlalchemy[asyncio]`, following
[SQLAlchemy's installation guidance](https://docs.sqlalchemy.org/en/21/faq/installation.html).
An isolated clean install resolved SQLAlchemy 2.1.6 and greenlet 3.5.6, with actual
async SQLite and `run_sync` queries passing. Nine local runtime/resource/dependency
contract tests pass. This is a narrow clean-install correction, not permission to
deploy the unfinished backend. Remote rerun results must be recorded separately.

Rerun [39991920472](https://github.com/Hyper-AI-Lab/cyber-ai-team/actions/runs/39991920472)
completed successfully for code commit `c15cc4c`: Backend (tests, Ruff, compile,
offline/real PostgreSQL migrations, dependency audit and migration rehearsal),
Frontend, Compose Smoke, Image Scan, Compose/Secrets/Diff and Observability Config
all passed. This verifies the committed security/dependency checkpoint, not the
uncommitted v5 lifecycle changes. No scheduled run or main-branch merge is claimed.

The Jev installer-only regression was pushed to our integration branch as
`83fb85b`. GitHub reported nine default-branch package alerts for MCP SDK
HTTP/OAuth/experimental-task features and Hono. These alerts are not findings
against Node 22.23.4; installed-path applicability remains unverified. No Jev
dependency bundle was silently updated as part of the Node runtime replacement.

## Next Development Step

Resume v5 step 2 from its existing checkpoint: bounded lifecycle reconciliation,
deterministic backfill and remaining namespace/concurrent-producer checks. Keep
the unfinished migration and unrelated dirty work intact; this maintenance does
not authorize deployment of that code or resumption of autonomy.
