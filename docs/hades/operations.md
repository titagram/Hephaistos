# Hades Operations

## Jobs

Backend jobs are pulled by Hades during manual sync and lightweight piggyback
sync. Jobs are capability-scoped and bounded to a linked workspace.

Initial read-only capabilities:

- `read_files`
- `project_inspection`
- `sync_git_tree`
- `populate_backend_ast`

Large or policy-gated jobs are stored as `waiting_confirmation` until the user
approves or the deadline expires.

Review local work with:

```bash
hades backend jobs
hades backend jobs --all
```

Approve only jobs you expect to run against the linked workspace:

```bash
hades backend approve-job <job_id>
```

If a job is too broad or no longer wanted, refuse it instead:

```bash
hades backend refuse-job <job_id> --reason "too broad"
```

Refused or conflicted memory proposals can be reviewed and acknowledged
locally:

```bash
hades backend proposals
hades backend ack-proposal <proposal_id>
```

## Artifacts

`sync_git_tree` produces `hades.git_tree.v1` artifacts with path, size, hash,
omission, retention, and truncation metadata. It skips env/secrets, ignored
files, generated dependency/build directories, binary/archive files, and files
above the configured per-file budget. `populate_backend_ast` produces
`hades.symbols.v1` artifacts with symbols and provenance, not raw source.

## Persephone

Persephone is the MVP realtime/inbox layer for communication between Hades
instances. It is not the primary job channel. Jobs remain pull/piggyback so
headless installations continue to work.

The first MVP contract is persistent inbox plus SSE or polling fallback. Hades
stores inbox events locally and surfaces unread/degraded state in status.
