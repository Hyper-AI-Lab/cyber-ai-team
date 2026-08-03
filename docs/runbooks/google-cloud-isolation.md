# Google Cloud Isolation Runbook

Cyber AI Team has no runtime dependency on Google Cloud. This runbook defines
the boundary that keeps owner identities and billing outside the application.

## Required State

- Core, worker, UI, Temporal, Qdrant, Redis, and ERPNext containers have no
  Google Cloud CLI binaries or Google credential files.
- No container mounts a host home directory, gcloud configuration, gsutil
  credential store, browser profile, or service-account key.
- Application and deployment environments do not define Google ADC or Cloud SDK
  credential override variables.
- Application code cannot invoke interactive cloud login, enable Google APIs,
  select a Google identity, or link a billing account.
- OAuth tokens, ADC documents, and service-account private keys are rejected by
  the repository secret scan.

Verify repository policy:

```bash
python3 scripts/secret-scan.py
python3 scripts/gcp-isolation-check.py
```

Verify running container mounts and environment keys without printing values:

```bash
docker inspect cyberteam-staging-core cyberteam-staging-worker \
  | jq -r '.[] | .Name, (.Mounts[]? | "  \(.Source) -> \(.Destination)"),
    ((.Config.Env // [])[] | split("=")[0]
      | select(test("GOOGLE|GCLOUD|GCP|BILLING"; "i")))'
```

## Future GCP Lab

A GCP lab is a separate security domain. Before provisioning it, the owner must
explicitly approve all of the following:

1. A dedicated non-personal identity with no access to owner projects.
2. A dedicated project or organization outside personal workloads.
3. A separate billing account with strict budget, quota, and alert limits.
4. An isolated Cloud SDK configuration and browser profile.
5. A written expiry and teardown date.
6. No credential mount, copy, memory ingestion, or workflow access from
   Cyber AI Team.

Do not reuse an owner browser session for OAuth consent. Do not let an autonomous
coding agent perform login, billing linkage, project creation, API enablement,
or credential export. Any failure caused by absent Google authentication is an
expected isolation control, not an error to repair automatically.
