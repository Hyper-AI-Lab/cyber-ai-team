# Production Readiness Closure Runbook

This runbook proves the selected single-owner staging scope before a production
promotion. It records evidence without storing secret values.

## Evidence Commands

Run from the repository root on the deployment host:

```bash
CYBERTEAM_ENV_FILE=deploy/environments/staging.env python3 scripts/github-ci-evidence.py
CYBERTEAM_ENV_FILE=deploy/environments/staging.env scripts/staging-restore-drill.sh
CYBERTEAM_ENV_FILE=deploy/environments/staging.env scripts/erpnext-backup.sh
CYBERTEAM_ENV_FILE=deploy/environments/staging.env scripts/erpnext-restore-drill.sh
CYBERTEAM_ENV_FILE=deploy/environments/staging.env scripts/load-smoke.sh
CYBERTEAM_ENV_FILE=deploy/environments/staging.env python3 scripts/business-workflow-smoke.py
```

Evidence is written to:

- `dist/ci/`
- `dist/restore-drills/staging/`
- `dist/erpnext/backups/`
- `dist/erpnext/restore-drills/`
- `dist/load-tests/`
- `dist/business-workflows/`

## Alert Delivery Proof

The observability profile includes Alertmanager. At runtime its email receiver is
generated from the ignored environment file (`SMTP_HOST`, `SMTP_PORT`,
`SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL`, and `OWNER_EMAIL`), while
`monitoring/alertmanager.yml` remains a non-secret syntax baseline for CI.

Use the owner console Operations page and click `Test Alert Email`, or call:

```bash
curl -fsS \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"dry_run": false, "note": "release gate"}' \
  https://cyberteam.hyperailab.com/api/operations/alerts/test-email
```

The backend sends one owner email through the configured SMTP provider and records
`control.evidence` with `control_id=alert_delivery.email`.

## Credential Rotation Evidence

Rotate secrets in the real secret store or ignored environment files first. Then
record evidence without secret values:

```bash
curl -fsS \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "scope": "staging",
    "secret_names": ["SECRET_KEY", "SMTP_PASSWORD", "ERPNEXT_API_SECRET"],
    "evidence_reference": "vault-change-123",
    "note": "Rotated by owner runbook.",
    "rotated_at": "2026-06-23T00:00:00Z"
  }' \
  https://cyberteam.hyperailab.com/api/operations/security/credential-rotation/evidence
```

Do not paste secret values into the request. The API stores secret names and the
operator reference only.

## Readiness Review

Open the Operations page or call:

```bash
curl -fsS \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  https://cyberteam.hyperailab.com/api/operations/readiness
```

For staging, the production-readiness closure cards should show fresh passing
evidence for CI, alert email, restore drills, load gate, and business workflow
smoke. Credential rotation may show `review_required` until operator rotation
evidence is recorded; missing or placeholder required secrets are blockers.
