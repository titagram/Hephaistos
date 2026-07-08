# Hades Doctor and Troubleshooting

## Doctor

Run:

```bash
hades doctor
```

The Hades Backend section checks registration, agent token presence, linked
workspaces, backend health, capabilities, job counts, proposal counts, and last
sync state.

Doctor does not send diagnostics to Laravel by default. To submit a compact
backend report explicitly, run:

```bash
hades doctor --report-backend
```

The report contains aggregate Hades state such as binding counts, job/proposal
counts, inbox counts, and last sync status. It does not include backend tokens,
job payload contents, or local absolute paths.

## Cleanup

Maintenance commands live under the doctor namespace. MVP cleanup includes
local-only cache/job/proposal/inbox cleanup. Cleanup is dry-run unless `--yes`
is present:

```bash
hades doctor cleanup --orphaned-cache
hades doctor cleanup --stale-jobs
hades doctor cleanup --stale-proposals
hades doctor cleanup --stale-inbox
hades doctor cleanup --orphaned-cache --all --yes
```

Cleanup does not delete backend memory. It only removes local stale or orphaned
state after confirmation. Refused or conflicted memory proposals remain visible
until they are acknowledged with `hades backend ack-proposal <proposal_id>`;
cleanup removes accepted or already-acknowledged proposal rows after the local
retention window.

## Degraded States

Common degraded states:

- backend unreachable
- token missing or revoked
- no linked workspace
- jobs in `waiting_confirmation`
- refused or conflicted memory proposals
- stale shared memory cache

Use `hades backend status --json` to get machine-readable actions.
For beta support and incident escalation, use
[support-runbook.md](support-runbook.md) so diagnostics stay token-free.

## Operator Runbooks

### Backend Unreachable Or Token Revoked

Run:

```bash
hades backend status --json
hades doctor
hades logs --level WARNING --session latest
```

Expected local evidence: `sync.last_error` or `sync.background.status=failed`
and a `sync.error` or `sync.client_error` warning in local logs. Re-run
`hades backend setup` only after confirming the backend URL and derived agent
token are wrong or revoked.

### Stuck Waiting Job

Run:

```bash
hades backend jobs
hades backend approve-job <job_id>
```

If the request is too broad, refuse it instead:

```bash
hades backend refuse-job <job_id> --reason "too broad"
```

Expected local evidence: `job_counts.waiting_confirmation > 0` and a status
action telling the user to review backend jobs.

### Refused Or Conflicted Memory Proposal

Run:

```bash
hades backend proposals
hades backend ack-proposal <proposal_id>
```

Expected local evidence: proposal status `refused` or `conflicted` with a
reason. Acknowledgement only silences local review state; it does not delete
backend memory.

### Artifact Too Large Or Truncated

Run:

```bash
hades backend sync
hades logs --level WARNING --session latest
```

Expected local evidence: an `artifact.uploaded` log event with
`hades_truncated=true` or nonzero `hades_redactions`, or a `sync.error` warning
if upload failed. Do not ask users to send raw source files as a workaround.

### Inbox Stale

Run:

```bash
hades backend sync
hades backend status --json
```

Expected local evidence: `inbox_counts.unread` changes after sync. If local
events are old and no longer useful, use `hades doctor cleanup --stale-inbox`
first as a dry run, then add `--yes` to remove stale local rows.
