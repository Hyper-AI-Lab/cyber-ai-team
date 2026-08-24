# Hosted LLM Capacity

Cyber-Team coordinates hosted completion traffic through Redis. API and Temporal
worker processes reserve slots from the same provider-wide key before every
completion attempt, including retries. This prevents independent schedulers from
creating a burst against an organization-wide hosted-provider limit.

## Runtime policy

- `LLM_HOSTED_PACING_ENABLED=true` enables fail-closed Redis coordination.
- `LLM_HOSTED_MIN_INTERVAL_SECONDS=20` sets the minimum start-to-start interval.
- `LLM_HOSTED_MAX_QUEUE_WAIT_SECONDS=300` bounds queued wait time.
- `LLM_RECOVERY_PROBE_ENABLED=true` enables read-only recovery evidence.
- `LLM_RECOVERY_PROBE_POLL_SECONDS=30` controls health polling.
- `LLM_RECOVERY_PROBE_COOLDOWN_SECONDS=120` bounds probe frequency across replicas.

If Redis coordination is unavailable, hosted inference stops with a classified
provider-availability error. Cyber-Team does not bypass pacing or report success.
Local inference is unaffected by hosted pacing and remains disabled unless the
owner explicitly enables it.

## Recovery behavior

A persisted retryable completion failure keeps readiness degraded. The API recovery
loop may then reserve one distributed cooldown claim and submit a minimal, read-only
completion with no memory recall, tools, ERPNext access, or communications. A real
successful response is persisted as `source_type=llm_provider_recovery_probe` and
immediately becomes newer provider-health evidence. Authentication failures and
capacity/payment failures are not probed automatically.

The Operations readiness LLM card reports:

- hosted pacing state and interval;
- persisted completion execution health;
- latest recovery probe state.

## Diagnosis

Inspect safe status without exposing credentials:

```bash
curl -fsS https://cyberteam.hyperailab.com/api/operations/readiness \
  -H "Authorization: Bearer $CYBERTEAM_ACCESS_TOKEN" \
  | jq '.integrations.llm'
```

Inspect recent recovery traces from the owner API or Memory timeline using source
type `llm_provider_recovery_probe`. A failed probe preserves only the normalized
error category; provider response payloads and credentials are not written to the
trace.

## Tuning

Increase the minimum interval when hosted 429 observations remain possible during a
soak. Decrease it only after a complete strict soak passes and hosted-provider limits
are known. Do not disable pacing to make a readiness blocker disappear.
