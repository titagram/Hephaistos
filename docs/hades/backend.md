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

## Bug Evidence

Bug reports and bug evidence are stored separately from generic shared memory.
Use bug evidence for root-cause investigation inputs such as stack traces, log
excerpts, failing tests, HTTP traces, browser console output, deploy versions,
config snapshots, user reproduction steps, and screenshot references.

The Hades v1 backend exposes:

- `POST /api/hades/v1/bug-reports`
- `GET /api/hades/v1/bug-reports/{bug_report_id}`
- `POST /api/hades/v1/bug-evidence`
- `GET /api/hades/v1/bug-evidence/search`

Each item is scoped to the authenticated project and linked workspace binding.
Evidence carries a kind, bounded summary/payload, source, sha256, redaction
count, retention class, and occurrence timestamp. This data is searchable by the
agent through a service-gated provider tool and is not injected into ordinary
automatic memory recall.

## Project Awareness

The backend exposes `GET /api/hades/v1/project-awareness/status` for a linked
workspace. The response reports freshness and coverage for shared memory,
artifacts, bug evidence, source slices, and code graph data. It also returns
`overall_status`, `diagnosable_without_source`, stale reasons, and concrete
actions.

The local agent can call the service-gated
`hades_backend_project_awareness_status` tool. Treat `stale`, `unknown`,
`missing`, or `partial` coverage as a hard warning before making exact root
cause, call-path, owner-method, or line-level claims without source access.
`hades backend sync` uploads artifacts with the linked workspace HEAD commit so
the backend can distinguish current indexes from stale ones.

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

`populate_backend_ast` uploads source-symbol artifacts without raw source:
`hades.php_graph.v1` for PHP/Laravel projects and `hades.symbols.v1` for Python
projects. The PHP graph includes route-handler, class/method, Eloquent
relation, static-call, and instantiation metadata so backend search can answer
structure questions without loading source chunks.

`proposals` defaults to refused or conflicted memory proposals. `ack-proposal`
marks one of those local proposals as acknowledged so status surfaces stop
reporting it as needing review.
