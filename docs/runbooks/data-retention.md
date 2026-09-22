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
- `RETENTION_AUDIT_OPERATIONAL_DAYS`
- `RETENTION_AUDIT_GOVERNANCE_DAYS`
- `RETENTION_AUDIT_SECURITY_DAYS`
- `RETENTION_BATCH_SIZE`

Set a day value to `0` or lower to disable age-based cleanup for that category. Memory
entries with `expires_at` in the past are still eligible for deletion. Pinned memories
are not removed by age-based memory retention.

`RETENTION_AUDIT_EVENT_DAYS` is retained as a compatibility setting. Current audit
retention uses the three category-specific windows. Eligible audit rows are never
discarded: they are moved atomically into compressed, SHA-256-verified archive bundles
partitioned by `operational`, `governance`, or `security` category.

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
- Deletes old communication logs, terminal workflow runs, and resolved approval
  requests according to their configured windows.
- Archives eligible audit events into immutable compressed category partitions before
  removing them from the hot audit table.
- Writes a `retention.cleanup` audit event with deletion/archive counts, archive IDs,
  and content hashes.

Each audit category may archive up to `RETENTION_BATCH_SIZE` events per cleanup run.
Security and governance history therefore retain longer hot-table windows by default,
while routine operational history moves into immutable archive storage sooner.

## Verify And Restore Audit Archives

List archives without exposing their payloads:

```bash
cd backend
cyber-team audit-archives --limit 100
cyber-team audit-archives --category security
```

Verify an archive and preview how many rows are missing from the hot table:

```bash
cyber-team audit-archive-restore audit_archive_example
```

Restore the missing rows after reviewing the preview:

```bash
cyber-team audit-archive-restore audit_archive_example --execute
```

The restore path decompresses the payload, verifies its SHA-256 hash and event count,
restores only absent original IDs, retains the archive bundle, and appends a separate
`retention.audit_archive_restored` audit event. The same operations are available to
the authenticated owner through:

- `GET /api/operations/retention/audit-archives`
- `POST /api/operations/retention/audit-archives/{archive_id}/restore`

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
