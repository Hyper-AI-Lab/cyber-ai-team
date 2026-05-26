# Data Retention and Subject Data Runbook

Cyber-Team stores operational records in PostgreSQL and semantic memory vectors in
Qdrant. The retention service applies bounded cleanup windows for application data
while preserving pinned memories and recording audit events for executed cleanup jobs.

## Retention Windows

Configure windows with environment variables:

- `RETENTION_MEMORY_DAYS`
- `RETENTION_COMMUNICATION_LOG_DAYS`
- `RETENTION_WORKFLOW_RUN_DAYS`
- `RETENTION_APPROVAL_REQUEST_DAYS`
- `RETENTION_AUDIT_EVENT_DAYS`
- `RETENTION_BATCH_SIZE`

Set a day value to `0` or lower to disable age-based cleanup for that category. Memory
entries with `expires_at` in the past are still eligible for deletion. Pinned memories
are not removed by age-based memory retention.

## Preview Cleanup

The CLI is dry-run by default:

```bash
cd backend
cyber-team retention-cleanup
```

The output reports candidate counts, cutoffs, and whether a category was truncated by
`RETENTION_BATCH_SIZE`.

## Execute Cleanup

Run cleanup after reviewing the preview:

```bash
cd backend
cyber-team retention-cleanup --execute
```

Executed cleanup:

- Deletes expired and old non-pinned memory records.
- Deletes matching Qdrant memory points when the memory service is available.
- Deletes old communication logs, terminal workflow runs, resolved approval requests,
  and audit events according to their configured windows.
- Writes a `retention.cleanup` audit event with deletion counts.

## Export Subject Data

Export structured records tied to a customer, person, or agent identifier:

```bash
cd backend
cyber-team subject-export customer-123 --output /tmp/customer-123-export.json
```

The export includes structured matches from memory entries, communication logs,
approval requests, and audit events. Matching is exact and identifier-based; it does
not attempt broad free-text discovery.

## Delete Subject Data

Preview deletion:

```bash
cd backend
cyber-team subject-delete customer-123
```

Execute deletion:

```bash
cd backend
cyber-team subject-delete customer-123 --execute
```

By default, historical audit events are retained and a new `data_subject.deleted`
audit event is written. Use `--include-audit` only when legal/compliance review confirms
that matching historical audit events should also be deleted.

## Operational Notes

- Run a PostgreSQL backup before bulk retention cleanup or subject deletion.
- Keep subject identifiers stable in tool payloads and metadata so export/delete can
  rely on structured matches.
- Use staging first when changing retention windows materially.
