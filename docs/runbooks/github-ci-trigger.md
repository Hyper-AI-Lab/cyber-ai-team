# GitHub CI Trigger Runbook

Cyber-Team can trigger GitHub Actions workflows through the `ci_trigger` tool.
This is an external side effect and remains approval-gated/manual-only in staging
and production.

## Required Configuration

Set these values in the ignored environment file:

```bash
GITHUB_TOKEN=<fine-grained-token-with-actions-workflow-write-access>
GITHUB_REPOSITORY=Hyper-AI-Lab/cyber-ai-team
GITHUB_DEFAULT_WORKFLOW=ci.yml
GITHUB_DEFAULT_REF=main
```

The token must be scoped narrowly to the target repository and must be able to
dispatch workflows.

## Readiness

When all four settings are present, `GET /api/tools` reports `ci_trigger` as
`live`. If any setting is missing, it reports `configuration_required`.

## Execution

`ci_trigger` accepts:

- `workflow`: optional workflow file or workflow id. Defaults to
  `GITHUB_DEFAULT_WORKFLOW`.
- `ref`: optional branch/tag/SHA. Defaults to `GITHUB_DEFAULT_REF`.
- `repository`: optional `owner/repo` override. Defaults to `GITHUB_REPOSITORY`.
- `inputs`: optional `workflow_dispatch` inputs.

The owner must approve the generated approval request before execution. Successful
execution returns HTTP 204 from GitHub and records an audit/tool trace event.

## Manual Smoke

Use a staging-only workflow or harmless workflow input when validating:

```bash
curl -X POST https://api.github.com/repos/Hyper-AI-Lab/cyber-ai-team/actions/workflows/ci.yml/dispatches \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer ${GITHUB_TOKEN}" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  -d '{"ref":"main","inputs":{}}'
```

Do not store the token in committed files or shell history on shared hosts.
