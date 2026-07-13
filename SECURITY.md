# Security Policy

Cyber AI Team is designed for owner-visible autonomy and auditability, but it is still evolving. Please treat it like sensitive infrastructure when deploying it.

## Reporting Vulnerabilities

Please report security issues privately through GitHub Security Advisories for this repository when available, or by opening a minimal issue that does not disclose exploit details and asking for a private contact path.

Do not include live credentials, access tokens, private URLs, customer data, or exploit payloads in public issues.

## Supported Version

The supported version is the latest commit on `main`.

## Security Expectations

- Replace all default secrets before non-local deployment.
- Prefer `OWNER_PASSWORD_HASH` over plaintext owner passwords.
- Restrict CORS to the exact owner-console origin in production.
- Keep staging/production external side effects approval-gated.
- Keep backups fresh before irreversible operations.
- Treat ERPNext as canonical business state and protect its admin credentials.
- Do not expose Docker, PostgreSQL, Redis, Qdrant, Temporal, or internal service ports publicly.
- Run the quality/release gates before deployment.

## Secret Handling

The repository includes a high-confidence secret scanner at `scripts/secret-scan.py`. Run it before commits:

```bash
python3 scripts/secret-scan.py
```

If a secret is accidentally committed, rotate it immediately. Removing it from the latest commit is not enough once the commit has been pushed.
