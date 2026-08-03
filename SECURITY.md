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

## Host And Cloud Isolation

Cyber AI Team does not require the Google Cloud CLI, Application Default
Credentials, a Google billing account, or a Google Cloud project. The deployed
containers must not mount host home directories, gcloud configuration, gsutil
credential stores, browser profiles, or cloud service-account keys.

Interactive cloud login, automatic ADC creation, automatic API enablement, and
automatic billing-account linking are prohibited in application code, scripts,
Compose configuration, and CI. This boundary is enforced by:

```bash
python3 scripts/gcp-isolation-check.py
```

If a future research or integration requirement genuinely needs GCP, provision
it as a separate lab with a non-personal identity, dedicated project, separate
billing account, explicit budget and quota limits, and isolated credentials.
Do not expose those credentials to Cyber AI Team or to an autonomous coding
agent with unrestricted host and browser access.

## Secret Handling

The repository includes a high-confidence secret scanner at `scripts/secret-scan.py`. Run it before commits:

```bash
python3 scripts/secret-scan.py
python3 scripts/gcp-isolation-check.py
```

If a secret is accidentally committed, rotate it immediately. Removing it from the latest commit is not enough once the commit has been pushed.
