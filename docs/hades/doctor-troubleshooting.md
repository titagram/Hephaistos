# Hades Doctor and Troubleshooting

## Doctor

Run:

```bash
hades doctor
```

Core Doctor is Backend-independent: it checks the Hades installation,
configuration, dependencies, tools, and services. It deliberately does not
import the optional Backend plugin, read Backend state, construct a Backend
client, or contact Laravel.

When the standalone project-knowledge plugin is installed and enabled, use its
explicit workspace-scoped status command instead:

```bash
hades backend status
```

Backend-specific reporting and cleanup are plugin-owned follow-ups. They are
not part of the core `hades doctor` interface.

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
`hades backend set-token --url URL --project-id ID` only after confirming the
Backend URL or derived credential is wrong or revoked. Run it from the linked
project root and enter the replacement token only through its masked prompt.

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
events are old and no longer useful, preserve them until the optional plugin
provides an explicit workspace-scoped cleanup operation.
