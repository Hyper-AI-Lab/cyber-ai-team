# ERPNext Runbook

Cyber-Team uses ERPNext as the canonical system of record for CRM, accounting,
projects, tasks, tickets, and procurement. The staging target runs ERPNext in the
Compose `erp` profile and exposes it privately through Caddy at
`erpnext.hyperailab.com`.

## Preconditions

- DNS for `erpnext.hyperailab.com` points to this host.
- Docker and Docker Compose are available on the host.
- The ignored staging env file exists at `deploy/environments/staging.env`.
- `deploy/environments/staging.env` contains live SMTP/IMAP settings and the
  ERPNext keys from `deploy/environments/staging.env.example`.
- Caddy is installed on the host and already manages the public Cyber-Team
  domains.

## Compose Validation

```bash
docker compose --profile erp config >/tmp/cyber-team-erp-compose-config.yml
```

This validates the ERPNext service graph without starting containers.

## Bootstrap ERPNext

```bash
ERPNEXT_ENV_FILE=deploy/environments/staging.env \
./scripts/bootstrap-erpnext.sh
```

The bootstrap script is idempotent. It:

- Ensures required ERPNext env keys exist in the ignored staging env.
- Generates missing ERPNext admin, MariaDB root, and site DB passwords.
- Starts the ERPNext `erp` profile.
- Creates the ERPNext site if it does not exist.
- Creates or updates the Cyber-Team integration user.
- Generates Frappe token credentials for that user.
- Validates REST token access.
- Writes `ERPNEXT_API_KEY` and `ERPNEXT_API_SECRET` to the ignored staging env.
- Writes non-secret evidence under `dist/erpnext/bootstrap/`.

Use `ERPNEXT_BOOTSTRAP_START_STACK=0` only when the ERPNext services are already
running and you want credential validation/bootstrap logic without another
Compose `up`.

## Caddy Exposure

Generate a Caddy basic-auth hash on the host. Prefer stdin so the plaintext
password does not appear in shell history or process arguments:

```bash
printf '%s\n' "$ERPNEXT_CADDY_BASIC_AUTH_PASSWORD" | caddy hash-password
```

Add this block to `/etc/caddy/Caddyfile`:

```caddyfile
erpnext.hyperailab.com {
    basicauth {
        cyberteam <caddy-hash>
    }

    reverse_proxy 127.0.0.1:18100
}
```

Then validate and reload Caddy:

```bash
caddy validate --config /etc/caddy/Caddyfile
systemctl reload caddy
```

ERPNext remains protected by both Caddy basic auth and ERPNext login. The
Cyber-Team backend talks to ERPNext through the private Compose service URL
`http://erpnext-frontend:8080`.

Public HTTPS validation requires DNS for `erpnext.hyperailab.com` to point to
this host before Caddy can obtain a public certificate. If DNS is not ready,
validate Caddy syntax, verify the local HTTP redirect, and complete the public
smoke after DNS propagation.

## Smoke Validation

Run the product-path Cyber-Team smoke first. This drives the real owner API,
approval queue, tool executor, ERPNext token integration, and cleanup/evidence
flow:

```bash
./scripts/erpnext-smoke.py \
  --env-file deploy/environments/staging.env \
  --api-base http://127.0.0.1:18000
```

The script refuses to run against a production Cyber-Team environment unless
`--allow-production` is explicitly passed. It creates staging-only Lead, Task,
Issue, and Material Request records through Cyber-Team tool execution, verifies
approval target and consumed-approval behavior, closes/archive-safe records, and
writes non-secret evidence under `dist/erpnext/smoke/`.

Check public login reachability:

```bash
curl -I -u "$ERPNEXT_CADDY_BASIC_AUTH_USER:$ERPNEXT_CADDY_BASIC_AUTH_PASSWORD" https://erpnext.hyperailab.com/login
```

Check private API token reachability from the Compose network:

```bash
docker compose --env-file deploy/environments/staging.env --profile erp exec -T core \
  python - <<'PY'
import asyncio
from cyber_team.integrations.erpnext import ERPNextClient

async def main():
    async with ERPNextClient() as client:
        result = await client.validate()
        print(result)

asyncio.run(main())
PY
```

Use staging-only records for live write smoke tests. Archive or cancel smoke
records after validation instead of deleting audit-relevant business history.

## Backup

Back up ERPNext MariaDB and site files before promotion or risky changes:

```bash
ERPNEXT_ENV_FILE=deploy/environments/staging.env \
./scripts/erpnext-backup.sh
```

The script runs `bench backup --with-files` inside `erpnext-backend`, copies the
database/public/private file artifacts into:

```text
backups/erpnext/staging/YYYYMMDDTHHMMSSZ/
```

It writes a manifest with filenames, sizes, and SHA-256 checksums to:

```text
backups/erpnext/staging/YYYYMMDDTHHMMSSZ/backup-manifest.json
```

It also writes non-secret operational evidence to:

```text
dist/erpnext/backups/erpnext-backup-YYYYMMDDTHHMMSSZ.json
```

## Restore Drill

Run the restore drill against the latest ERPNext backup artifact:

```bash
ERPNEXT_ENV_FILE=deploy/environments/staging.env \
./scripts/erpnext-restore-drill.sh
```

To restore a specific backup manifest:

```bash
ERPNEXT_ENV_FILE=deploy/environments/staging.env \
ERPNEXT_RESTORE_BACKUP_MANIFEST=backups/erpnext/staging/YYYYMMDDTHHMMSSZ/backup-manifest.json \
./scripts/erpnext-restore-drill.sh
```

The script creates a temporary ERPNext site named
`restore-drill-YYYYMMDDTHHMMSSZ.local`, restores the database and site files into
that site, runs `bench migrate`, validates the REST ping endpoint through the
ERPNext frontend, records key DocType counts, verifies the Cyber-Team integration
user exists, and then drops the temporary site.

Restore evidence is written under `dist/erpnext/restore-drills/` with:

- Backup filenames and checksums.
- Temporary site name.
- Restore start/end timestamps.
- `bench --site <temporary-site> migrate` output.
- API validation result against the temporary site.
- Restored Lead, Task, Issue, Material Request, Item, Company, and User counts.
- Temporary-site cleanup status.

## Rollback

For application rollback, use the existing release rollback runbook:
`docs/runbooks/release-rollback.md`.

For ERPNext rollback:

1. Stop Cyber-Team workers that can write to ERPNext.
2. Take a current backup for forensic preservation.
3. Restore the last known-good ERPNext database and files.
4. Run `bench --site <site> migrate`.
5. Re-run Cyber-Team ERPNext validation.
6. Restart workers only after validation passes.

Do not delete audit or compliance-relevant ERPNext records during rollback unless
there is an explicit owner-approved retention or GDPR deletion record.
