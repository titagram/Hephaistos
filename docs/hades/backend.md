# Hades Backend Setup

## Gnothi Seauton: local organism awareness

`gnothi_seauton` is the local, evidence-backed description of the installed
Hades organism. It inventories source anatomy, capabilities, runtime state,
protected contracts, declared dependencies, and bounded experience events into
immutable `hades.organism_graph.v1` revisions under
`$HERMES_HOME/gnothi_seauton/`.

Operator commands:

```bash
hades gnothi-seauton status --json
hades gnothi-seauton rebuild --workspace /path/to/workspace --json
hades gnothi-seauton rebuild --workspace /path/to/workspace --collector capabilities
hades gnothi-seauton inspect <component-id> --json
hades gnothi-seauton explain <capability-id> --json
hades gnothi-seauton diff <revision-a> <revision-b> --json
hades gnothi-seauton wiki
```

The conversational `/gnothi_seauton` command is also available in the classic
CLI, messaging gateway, and TUI. It submits a normal user turn, preserving the
conversation's cached system-prompt prefix and tool schema. It is read-only:
it may inspect the current revision and graph tools with `scope=organism`, but
it does not rebuild, research, download, install, mutate configuration, or
start an evolution.

Each revision contains an `organism_contract` with generation identity,
semantic fingerprint, per-collector coverage, and freshness. Coverage states
are:

- `current`: the collector completed and its cheap input fingerprint still
  matches;
- `stale`: the stored fingerprint differs from the current probe;
- `partial`: collection completed with missing evidence or a bounded failure;
- `missing`: the domain has no usable evidence;
- `unknown`: status-time probing could not establish freshness.

`status` reports invalidated domains and suggests targeted `--collector`
refreshes. Targeted rebuilds preserve unselected domains and their original
freshness, while immutable prior revisions remain available for `diff` and
rollback analysis. The generated wiki is derived entirely from the artifact;
its evidence links point to the artifact's bounded evidence index and manual
wiki edits are not authoritative.

The local collector never stores raw source bodies, skill bodies, secret
values, or absolute local paths. Experience input is limited to the structured,
bounded `$HERMES_HOME/logs/organism-events.jsonl` stream. When the backend
database does not already exist, runtime inspection reports the backend as
unconfigured without creating that database.

Backend publication is deliberately not automatic in this local slice. The
canonical backend graph must accept the versioned organism contract and keep it
separate under `scope=organism`; code-graph search remains under its existing
scope. Until the matching backend contract is committed and verified, do not
assume that ordinary `hades backend sync` uploads organism revisions. No new
route, migration, deploy, restart, or database change is required to use the
local commands.

Troubleshooting:

- `status=missing`: run a full local `rebuild` from the intended workspace;
- one domain is `stale`: use the suggested targeted collector refresh;
- `contracts=partial`: the installed checkout lacks one or more files named by
  the versioned invariant manifest;
- `experience=missing`: no bounded failure event has been recorded yet;
- an `error_class` is shown: rerun the named collector and inspect local logs;
  artifact errors never include raw exception messages.

`gnothi_seauton` is a prerequisite for reasoning about future evolution. It
does **not** implement `autopoiesis`, self-modification, external tool research,
approval workflows for evolution, or self-versioned code rollback.

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

## Kanban Task Work Contract

Dashboard Kanban tasks can become local Hades work only through the versioned
`hades.kanban_task_work.v1` payload stored on `agent_work_items`. This payload
is the shared contract between backend task authors, backend-side agents, and
the local agent worker.

Required fields:

- `schema`: always `hades.kanban_task_work.v1`.
- `task_id`, `project_id`: authoritative dashboard identifiers.
- `repository_id`: repository scope selected on the task, or `null` only before
  a task is ready for local agent work.
- `title`, `description`, `acceptance_criteria`, `priority`, `risk`: bounded
  task text copied from the dashboard.
- `normalized_problem`: executable problem statement derived from the task.
- `task_type`: one of `implementation`, `analysis`, or `bug`.
- `clarification_status`: `ready` or `needs_clarification`.
- `ready_for_agent_work`: true only when repository scope, observable problem,
  and acceptance criteria are present.
- `required_context`: context classes the agent must consult, such as
  `shared_project_memory`, `project_awareness_status`, `repository_scope`, and
  `bug_evidence`.
- `source_access_policy`: source-free-first policy, including whether approved
  source-slice jobs are allowed.
- `project_awareness_required` and `memory_required`: both true for local Hades
  work items.
- `created_from`: provenance with `type=kanban_task`, source, assigning user,
  and normalization timestamp.

Bug or root-cause tasks add:

- `workspace_binding_id`: linked Hades workspace binding when known.
- `bug_report_id`: Hades bug report created for the task when a linked binding
  exists.
- `evidence_refs`: at least the initial `bug_evidence` ref when evidence was
  created from the task.
- `bug_intake`: status object. Valid statuses are `not_applicable`, `created`,
  `existing`, and `missing_workspace_binding`.

Lifecycle:

1. The dashboard validates the task and repository scope.
2. Backend normalization derives `normalized_problem`, `task_type`,
   `required_context`, clarification questions, and readiness.
3. Tasks with `clarification_status=needs_clarification` are not queued for
   `local_agent`; they must be clarified first.
4. Ready bug/root-cause tasks create or reuse Hades bug report and initial
   evidence when the project has a linked Hades workspace binding.
5. Ready tasks create one active `agent_work_items` row for
   `assigned_agent_key=local_agent`.
6. The backend writes a `queued_from_kanban_task` work-item event containing the
   schema, normalization summary, and bug-intake status.

The local worker must treat this payload as authoritative task input, but it
must still check shared project memory and project awareness before making
source-free diagnosis claims.

The local CLI carries a matching contract validator for
`hades.kanban_task_work.v1`. `hades backend tasks list --json` includes a
`contract` object for kanban task payloads with `valid=true` or stable
field-level errors. The worker can build bounded prompt input from the contract
payload even when the backend does not send a legacy free-form `prompt` field.
This is the local release gate that catches backend/local payload drift before
an item is claimed.

No-codebase release fixtures can be attached to a task payload with optional
`quality_eval.no_codebase_fixture_id`. When present, the Hades backend
`quality-report` command with `--no-codebase-eval <fixture.json>` also
evaluates the completed work item result as a no-codebase diagnosis run.
Completed bug work must then store a structured `result.no_codebase_diagnosis`
with freshness, awareness, evidence refs, Hades retrieval tool calls, causal
pack refs, causal chain, and persisted report status. A prose-only answer is
intentionally treated as a quality blocker.

## Bug Evidence

Bug reports and bug evidence are stored separately from generic shared memory.
Use bug evidence for root-cause investigation inputs such as stack traces, log
excerpts, failing tests, HTTP traces, browser console output, deploy versions,
config snapshots, user reproduction steps, and screenshot references.
Use `hades backend bug-intake --title ... --symptom ...` from a linked
workspace to create a structured bug report and optionally attach `--test-output`
or `--log` files as bounded, redacted evidence. Test/log evidence includes
stack frames, `frame_refs`, a redacted `excerpt_sha256`, and log frame refs so
graph/source-slice search can correlate evidence without parsing raw log text.
Include `--deploy-commit <sha>`
or run `hades backend ingest-deploy --deploy-commit <sha>` to store a
`deploy_version` item; the payload explicitly flags whether the deployed commit
differs from the linked workspace head. Include `--request-url <url>` on
`bug-intake` or run `hades backend ingest-http --url <url>` to store
`http_request`/`http_response` context with redacted URL and bounded request or
response excerpts.

The Hades v1 backend exposes:

- `POST /api/hades/v1/bug-reports`
- `GET /api/hades/v1/bug-reports/{bug_report_id}`
- `POST /api/hades/v1/bug-evidence`
- `GET /api/hades/v1/bug-evidence/search`
- `POST /api/hades/v1/evidence-packs`
- `GET /api/hades/v1/evidence-packs`
- `POST /api/hades/v1/causal-packs`
- `GET /api/hades/v1/causal-packs`
- `GET /api/hades/v1/causal-packs/{causal_pack_id}`
- `POST /api/hades/v1/causal-packs/{causal_pack_id}/replay`

Each item is scoped to the authenticated project and linked workspace binding.
Evidence carries a kind, bounded summary/payload, source, sha256, redaction
count, retention class, and occurrence timestamp. This data is searchable by the
agent through a service-gated provider tool and is not injected into ordinary
automatic memory recall.
Causal packs are narrower than evidence packs: they are replayable proof
bundles for one root-cause claim. They store refs to bug evidence, graph facts,
source slices, freshness, awareness state, affected refs, and diagnosis
taxonomy. They do not store full source files and should be replayed before a
source-free high/medium diagnosis is trusted.

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
`diagnosis_awareness_not_diagnosable`. The local agent provider also refreshes
live project-awareness status before saving high/medium reports, so stale
coverage cannot be bypassed by passing claimed-current freshness in tool
arguments.
For source-free high/medium reports, the backend additionally requires
`causal_pack_refs` that resolve to a replayable causal pack for the same
project/workspace evidence. Missing or invalid pack refs must downgrade the
result to insufficient rather than allowing a precise claim.

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
artifacts, bug evidence, source slices, code graph data, and causal packs. It
also returns `overall_status`, `diagnosable_without_source`, stale reasons, and
concrete actions.

The local agent can call the service-gated
`hades_backend_project_awareness_status` tool. Treat `stale`, `unknown`,
`missing`, or `partial` coverage as a hard warning before making exact root
cause, call-path, owner-method, or line-level claims without source access.
`hades backend sync` uploads artifacts with the linked workspace HEAD commit so
the backend can distinguish current indexes from stale ones.

Graph artifacts may include metadata-only `source_slice_candidates`. These are
not raw source content. The backend deduplicates them by candidate key, exposes
their pending/approved coverage in project awareness, and can create
confirmation-gated `read_source_slice` jobs for the local checkout. Pending
candidates are an explicit awareness action: the agent should approve the
bounded slice job from a source-owning device, or keep the diagnosis
insufficient when exact source evidence is required.

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

Diagnosis reports can also carry a structured taxonomy:
`root_cause_id`, `bug_class`, `failure_classification`, and `affected_refs`.
These fields are persisted in the diagnosis payload, carried into promoted
resolved-bug memory, and indexed for later no-codebase searches. Prefer stable,
project-local `root_cause_id` values so recurring failures can be matched
without relying on brittle prose similarity.

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
query calls, and redacted `config()`/`env()` references. For diagnostic
traversal, PHP graph artifacts keep the existing class-level edges and also add
method-level duplicates for calls, validation, DB queries, config/env refs,
view refs, job/event dispatch, and instantiation when a method context is
recognizable. The code graph includes
framework, route/page handlers, symbols, dependency manifests, and import edges
so backend search can answer structure questions without loading source chunks.
Graph artifacts also include a source-free `tests` map for recognized test
files, with test framework, case names/lines, target candidates, and edges from
`test:<path>` nodes to already-indexed routes, symbols, and imports.
Python web graphs include AST-derived `imports` and `calls` edges so handlers
can be connected to service/repository/client calls without storing function
bodies or arguments.
PHP, Python, and Node/TypeScript graph artifacts also include a metadata-only
`logs` map for logging calls, storing level/logger/context/path/line and a
redacted message hash rather than the message template.
Before sending a large artifact, the local sync can call
`/api/hades/v1/artifacts/lookup` with project, workspace binding, schema, and
sha256. A positive lookup means the backend already has the same artifact for
that binding, so the client records a skip instead of uploading the payload.
The artifact POST endpoint also deduplicates repeated hashes server-side.

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

`hades backend quality-report` includes local `note_backfill` metrics for these
proposals. Pending or submitted candidates keep the report in `attention` until
review closes; refused or conflicted proposals and missing evidence refs are
also surfaced in the action queue.

Quality reports can also load a suite manifest with `--suite`. A suite groups
one or more no-codebase evaluation fixtures and records aggregate gates such as
root-cause accuracy, forbidden source-access violations, persistence coverage,
tool-order coverage, taxonomy coverage, causal pack coverage, causal chain
coverage, and counterfactual refusal coverage. Use suites for regression checks
before claiming that a project is ready for source-free diagnosis.

`proposals` defaults to refused or conflicted memory proposals. `ack-proposal`
marks one of those local proposals as acknowledged so status surfaces stop
reporting it as needing review.
