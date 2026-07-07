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
Use `hades backend bug-intake --title ... --symptom ...` from a linked
workspace to create a structured bug report and optionally attach `--test-output`
or `--log` files as bounded, redacted evidence.

The Hades v1 backend exposes:

- `POST /api/hades/v1/bug-reports`
- `GET /api/hades/v1/bug-reports/{bug_report_id}`
- `POST /api/hades/v1/bug-evidence`
- `GET /api/hades/v1/bug-evidence/search`
- `POST /api/hades/v1/evidence-packs`
- `GET /api/hades/v1/evidence-packs`

Each item is scoped to the authenticated project and linked workspace binding.
Evidence carries a kind, bounded summary/payload, source, sha256, redaction
count, retention class, and occurrence timestamp. This data is searchable by the
agent through a service-gated provider tool and is not injected into ordinary
automatic memory recall.

The backend refuses content-bearing diagnosis data that is too large or appears
to contain unredacted credentials. The current safety baseline caps bug evidence
payloads at 64 KB, source slices at 64 KB, diagnosis report payloads at 32 KB,
and evidence packs at 96 KB. Rejected payloads return a structured error such
as
`unredacted_secret_detected`, `evidence_payload_too_large`,
`source_slice_too_large`, `evidence_pack_payload_too_large`, or
`diagnosis_payload_too_large`.
High or medium confidence diagnosis reports are also rejected unless
`freshness.status=current`, `evidence_refs` is non-empty, and current project
awareness is `diagnosable_without_source=true`; failures return
`diagnosis_freshness_not_current`, `diagnosis_evidence_refs_required`, or
`diagnosis_awareness_not_diagnosable`.

Privacy and retention controls are explicit and workspace scoped. The backend
exposes:

- `GET /api/hades/v1/privacy/export`
- `POST /api/hades/v1/privacy/delete`
- `POST /api/hades/v1/privacy/retention-cleanup`

All three require the authenticated agent, project id, and linked
`workspace_binding_id`. Export can omit content fields with
`include_content=false`; the local CLI does this by default through
`hades backend privacy-export --json`. Delete and retention cleanup are dry-run
by default and require `confirm=true` backend-side; the CLI sends that only when
the user passes `--yes`.
Each successful export, delete dry-run/delete, and retention dry-run/delete
writes an `audit_logs` row with action, agent id, scope, counts, dry-run flag,
and retention window metadata only. Audit payloads must not contain source
slices, evidence payloads, diagnosis text, or other raw content.

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

## Resolved Bug Memory

Final high/medium confidence diagnosis reports can be promoted through
`POST /api/hades/v1/diagnosis-reports/{diagnosisReport}/promote`. Promotion is
allowed only after explicit verification (`user_confirmed`, `test_passed`, or
`manual_review`) and creates a `project_memory_entries` record with
`kind=resolved_bug` and `source=hades_diagnosis_report`.

Resolved bug memory stores symptom, root cause, mechanism, evidence refs,
affected symbols, fix/regression-test metadata, and a validity window. Memory
search boosts `resolved_bug` entries for similar bug queries and marks them
`stale` when the linked workspace HEAD no longer matches the commit captured by
the diagnosis.

## Graph Traversal

`GET /api/hades/v1/graph/traverse` traverses the current stored code graph for a
linked workspace. It starts from a route, symbol, file, class, or method and
returns bounded nodes/edges with match fields, artifact provenance, freshness,
and the graph artifact HEAD commit.

The local agent exposes this through `hades_backend_graph_traverse`. Use it
after bug evidence identifies an entrypoint and before source-slice fetch when
the diagnosis needs route -> controller -> service/model context without local
source access. `hades_backend_graph_search` can find candidate graph nodes and
edges before traversal. When live backend calls are unavailable, both provider
tools can fall back to synced local graph artifacts; fallback responses are
explicitly marked as cached and should not be treated as fresh/live evidence.

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

The JSON status includes job counts, proposal counts, sync state, local
`awareness` health, and actions for waiting jobs, refused proposals, degraded
sync state, and incomplete local project awareness. Each binding includes
coverage for memory cache, project artifacts, source slices, and bug evidence.
`diagnosable_without_source` is intentionally conservative: it is false until
the local profile has a linked workspace, cached memory, uploaded project
artifacts, uploaded source slices, known bug evidence, and no recorded sync
errors.
If a project artifact is unchanged, sync may skip re-uploading it and report
`skipped_unchanged_last_sync`; that still counts as project artifact coverage
because the backend already has the content-addressed artifact for the same
workspace/schema/head.
When a profile has multiple workspace bindings, last-sync counters are marked
as aggregate and do not make any single binding source-free diagnosable.
The same payload includes `identity` to separate local profile memory,
backend project memory, and the current local workspace binding. Project memory
is portable across devices once the backend agent is configured; local profile
memory and workspace freshness remain device-local. `identity.login_recovery`
adds a current-device next action so a newly logged-in device can see whether it
can use backend project memory immediately, needs to link the current
workspace, or must sync/capture evidence before source-free diagnosis.

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
`hades.php_graph.v1` for PHP/Laravel projects, `hades.code_graph.v1` for
Node/TypeScript/React/Next/Express projects, and `hades.symbols.v1` for Python
projects. The PHP graph includes route-handler and route-middleware edges,
class/method symbols, Eloquent relations, static calls with simple `use`
resolution, instantiation metadata, model-to-table edges, migration
tables/columns/indexes/foreign keys, policy mappings, FormRequest/request
validation fields, dispatched jobs, emitted events, event listeners, Artisan
command signatures, scheduler command/job edges, query-table edges, Eloquent
query calls, and redacted `config()`/`env()` references. The code graph includes
framework, route/page handlers, symbols, dependency manifests, and import edges
so backend search can answer structure questions without loading source chunks.

`read_source_slice` is the bounded source-content path for diagnosis: it is not
auto-executed by piggyback sync, stores only a selected redacted line window as
`source_slice`, and keeps that content out of automatic project-memory recall.

`hades backend backfill-note <path>` is a local note-quality preview for old raw
chunks or ad-hoc notes. It classifies `hades.backend_wiki.file_chunk.v1`
content as raw, extracts candidate facts such as grouped route-handler
relationships, and keeps the result review-only. It does not create project
memory or enable automatic recall for raw chunks. Add `--create-proposals` only
when running from a linked workspace and you want those candidate facts saved as
pending memory proposals for backend review. After sync, note backfill
proposals are treated as submitted locally but remain pending in the backend;
they are not auto-created as project memory.

`proposals` defaults to refused or conflicted memory proposals. `ack-proposal`
marks one of those local proposals as acknowledged so status surfaces stop
reporting it as needing review.
