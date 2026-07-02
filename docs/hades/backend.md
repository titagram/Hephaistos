# Hades Backend Setup

## Commands

`hades backend bootstrap` is the preferred setup path. It registers the local
agent, persists the derived token, creates or reuses a local project, links the
workspace, and runs an initial sync.

`hades project link <project>` links an existing local project to the backend.
It sends a redacted display path, workspace fingerprint, git remote display,
remote hash, and HEAD commit. The backend returns the stable
`workspace_binding_id`.

`hades project unlink <project>` notifies the backend, then disables the local
binding without deleting shared memory or job history.

## Shared Memory

The backend owns shared memory. Hades reads versioned snapshots into a local
cache and writes create/update/delete requests as memory proposals. Hades does
not directly mutate shared backend memory; the backend accepts, refuses, or
conflicts each proposal. Update and delete proposals carry local memory identity
and base version or etag in proposal provenance when the local memory tool
provides them.

If the backend refuses or conflicts a proposal, local status must show the
reason. If the backend is unavailable, Hades uses local memory and may use stale
shared memory cache as degraded context.

## Status

Use:

```bash
hades backend status
hades backend status --json
hades backend sync
hades backend jobs
hades backend approve-job <job_id>
hades backend refuse-job <job_id> --reason "too broad"
hades backend proposals
hades backend ack-proposal <proposal_id>
```

The JSON status includes job counts, proposal counts, sync state, and actions
for waiting jobs, refused proposals, and degraded sync state.

`jobs` defaults to `waiting_confirmation` work. `approve-job` executes a stored
waiting job in the linked workspace, submits the result or artifact metadata,
and records the final local status. `read_files` jobs only send bounded,
redacted source content after this explicit local confirmation and mark the
result as `retention_class=source_content`; artifact jobs upload metadata and
symbols, not raw source. `project_inspection` is metadata-only in the local MVP:
it uploads a `hades.git_tree.v1` artifact marked `inspection_mode=metadata_tree`,
not a synthesized answer. `refuse-job` marks a waiting job cancelled and sends a
redacted reason to the backend.

`proposals` defaults to refused or conflicted memory proposals. `ack-proposal`
marks one of those local proposals as acknowledged so status surfaces stop
reporting it as needing review.
