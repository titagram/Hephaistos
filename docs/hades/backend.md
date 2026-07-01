# Hades Backend Setup

## Commands

`hades backend bootstrap` is the preferred setup path. It registers the local
agent, persists the derived token, creates or reuses a local project, links the
workspace, and runs an initial sync.

`hades project link <project>` links an existing local project to the backend.
It sends a redacted display path, workspace fingerprint, git remote display,
remote hash, and HEAD commit. The backend returns the stable
`workspace_binding_id`.

`hades project unlink <project>` disables the local binding without deleting
shared memory or job history.

## Shared Memory

The backend owns shared memory. Hades reads versioned snapshots into a local
cache and writes create/update requests as memory proposals. Hades does not
directly delete shared backend memory.

If the backend refuses or conflicts a proposal, local status must show the
reason. If the backend is unavailable, Hades uses local memory and may use stale
shared memory cache as degraded context.

## Status

Use:

```bash
hades backend status
hades backend status --json
hades backend sync
```

The JSON status includes job counts, proposal counts, sync state, and actions
for waiting jobs, refused proposals, and degraded sync state.
