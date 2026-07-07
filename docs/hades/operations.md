# Hades Operations

## Jobs

Backend jobs are pulled by Hades during manual sync and lightweight piggyback
sync. Jobs are capability-scoped and bounded to a linked workspace.

Initial read-only capabilities:

- `read_files`
- `read_source_slice`
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

Local memory writes are mirrored as backend proposals, not direct backend
mutations. Adds become `create` proposals, replacements become `update`
proposals, and removals become `delete` proposals. When available, Hades stores
the local `memory_id` and `base_version`/`etag` in proposal provenance so the
backend can reject stale or mismatched mutations instead of applying them
silently.

## Sync

`hades backend sync` remains the manual repair path and bypasses any background
backoff. A successful manual sync clears stale background-sync failure state.

Normal agent turns start a lightweight piggyback sync when a profile has a
linked backend workspace and the per-profile backoff window is due. The
piggyback run is asynchronous, quiet, and fail-open: chat continues even if the
backend is offline. Repeated failures are recorded in local sync state and
surface as a degraded backend action in `hades backend status --json`.
The same JSON payload also exposes local `awareness` health per workspace
binding. Use it as the local readiness view for memory cache, artifact upload,
source-slice upload, and bug-evidence availability before attempting
source-free diagnosis. It does not perform live backend calls; for backend
freshness and coverage, use the project-awareness tool/API below.
Artifact upload is content-addressed locally per workspace binding, schema, and
HEAD commit. If `sync_git_tree` or `populate_backend_ast` produces the same
artifact twice, Hades logs `artifact.skipped`, records
`artifacts_skipped`/`skipped_unchanged_last_sync`, and avoids a duplicate
backend upload while keeping local awareness coverage present.
Its `identity` section separates local profile memory, portable backend project
memory, and local workspace binding state so a new device can distinguish
shared project recall from source/index freshness that must be established on
that device. The same section includes `login_recovery.recommended_next_action`
for the current device, such as linking the workspace, running backend sync, or
capturing evidence/source slices before source-free diagnosis.

## Bug Evidence

Bug evidence is the first production slice for no-codebase root-cause
investigation. Store observations as typed evidence instead of generic memory
notes:

- `stack_trace`
- `log_excerpt`
- `failing_test`
- `http_request`
- `http_response`
- `browser_console`
- `deploy_version`
- `config_snapshot`
- `user_steps`
- `screenshot_ref`

Evidence is project/workspace scoped and should stay bounded and redacted. The
local agent can search it with the `hades_backend_bug_evidence_search` provider
tool when diagnosing a bug. There is intentionally no local cache fallback for
bug evidence search: stale or unavailable evidence must be surfaced as degraded
state rather than treated as authoritative.

For guided CLI intake, run `hades backend bug-intake --title ... --symptom ...`
from a linked workspace. Optional `--steps`, `--expected`, `--actual`,
`--severity`, and `--environment` fields are stored in a bounded
`hades.bug_intake.v1` payload. Repeat `--test-output <file>` and `--log <file>`
to attach redacted `failing_test` and `log_excerpt` evidence to the created bug
report.

Diagnosis outcomes should be persisted with
`hades_backend_diagnosis_report_create` / `POST /api/hades/v1/diagnosis-reports`
once the workflow reaches either a supported root cause or a useful
insufficient-evidence result. Reports carry confidence, root cause, runtime
mechanism, evidence refs, freshness, bounded payload, and redaction count.

The backend enforces a shared evidence safety policy before storing
content-bearing diagnosis data:

- bug evidence payloads are capped at 64 KB and rejected when they contain
  unredacted bearer tokens, API keys, cookies, passwords, private keys, or
  obvious secret assignments;
- source slices are capped at 64 KB, must be bounded by the line-window policy,
  and are rejected if redaction failed to remove secrets;
- diagnosis report payloads are capped at 32 KB and are also checked for
  unredacted secrets.

## Project Awareness Gate

Use `hades_backend_project_awareness_status` from the agent, or the backend API
`GET /api/hades/v1/project-awareness/status`, before precise source-free bug
diagnosis. The status distinguishes:

- `freshness.status`: `current`, `stale`, `missing`, or `unknown`.
- `coverage.memory`, `coverage.artifacts`, `coverage.bug_evidence`,
  `coverage.code_graph`, and `coverage.source_slices`.
- `diagnosable_without_source`: true only when the backend has enough current
  evidence to support exact source-free diagnosis.

If freshness is stale/unknown or coverage is missing/partial, the agent should
state that limitation and gather/index the missing evidence before claiming a
precise cause. A successful `hades backend sync` sends artifact HEAD metadata
from the linked workspace binding so the backend can clear stale artifact
warnings when the index matches the current checkout.
The backend and local diagnosis tool both reject high/medium confidence
diagnosis reports when this status is not source-free diagnosable; save `low`
or `insufficient` until current graph, bug evidence, and source-slice coverage
exist.

Agent live Hades tools are intentionally fail-fast. Lookup/status/search paths
use a 1 second backend timeout, source-slice fetch uses 1.5 seconds, and
write/create/promote paths use 2 seconds. When a live lookup times out or the
backend is unavailable, the tool must return an explicit degraded/unavailable
state instead of silently treating cache or generic memory as current evidence.

## Lifecycle And Cleanup

Local Hades backend state has explicit retention classes:

| State | Local retention | Cleanup |
| --- | --- | --- |
| Waiting jobs | Kept until approved, refused, or `deadline_at` expires | `hades backend approve-job`, `hades backend refuse-job`, automatic expiry during sync |
| Terminal jobs (`completed`, `failed`, `expired`, `cancelled`, `unlinked`) | 30 days after last update | `hades doctor cleanup --stale-jobs` |
| Pending memory proposals | Kept until backend accepts/refuses/conflicts them | `hades backend sync` |
| Refused/conflicted memory proposals | Kept until local review | `hades backend ack-proposal <proposal_id>` |
| Accepted/acknowledged memory proposals | 90 days after last update | `hades doctor cleanup --stale-proposals` |
| Orphaned shared-memory cache | 90 days after unlink | `hades doctor cleanup --orphaned-cache` |
| Local Persephone inbox events | 30 days after receipt | `hades doctor cleanup --stale-inbox` |
| Artifact payloads | Not retained locally after upload | Backend artifact retention policy |
| Doctor reports | Not retained locally after explicit submit | Backend doctor-report retention policy |
| Backend bug evidence, source slices, evidence packs, diagnosis reports | Backend workspace scoped policy | `hades backend privacy-export`, `hades backend privacy-delete`, `hades backend retention-cleanup` |

Cleanup is dry-run by default. Add `--yes` to remove rows and
`--retention-days <days>` to override the selected local retention window for a
one-off maintenance run. `--all` includes non-expired selected candidates, but
does not delete active jobs or unreviewed refused/conflicted proposals.

Backend privacy cleanup is also dry-run first:

```bash
hades backend privacy-export --json
hades backend privacy-export --include-content --json
hades backend privacy-delete --json
hades backend privacy-delete --yes --json
hades backend retention-cleanup --retention-days 30 --json
hades backend retention-cleanup --retention-days 30 --yes --json
```

`privacy-export` defaults to metadata-only so support can inspect counts and
ids without dumping redacted source slices, evidence payloads, or diagnosis
text. `privacy-delete` removes only the current linked workspace's Hades bug
reports, evidence items, source slices, evidence packs, and diagnosis reports.
`retention-cleanup` removes only scoped rows older than the requested retention
window.
The backend audits every successful privacy export/delete/retention request in
`audit_logs` with scope, agent id, counts, dry-run flag, and retention metadata
only; raw evidence, source slices, and diagnosis text are never copied into the
audit payload.

## MVP Smoke

The deterministic no-network MVP smoke composes local setup state, shared-memory
snapshot/proposal sync, job execution, artifact upload, inbox polling, doctor
reporting, and the TUI/backend status payload:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/hermes_cli/test_hades_backend_mvp_smoke.py
```

For live staging smoke, use a disposable `HERMES_HOME` and a backend dashboard
bootstrap command for a test project. Then run:

```bash
hades backend status --json
hades backend support-report --json
hades backend sync
hades doctor --report-backend
```

Use `support-report` for tickets; it keeps backend status, awareness, sync, and
action fields while redacting local absolute paths and likely secrets. Do not
paste project bootstrap tokens, derived agent tokens, raw job payloads, or local
absolute paths into logs or support tickets.

For release or periodic governance checks, run:

```bash
hades backend quality-report --no-codebase-eval tests/fixtures/hades/no_codebase_bug_cases.json --json
```

The quality report produces `hades.quality_report.v1` with no-codebase diagnosis
metrics, including required evidence refs, required Hades tool calls, persisted
diagnosis reports, forbidden source-access tool use, and diagnosis freshness
coverage plus source-free awareness coverage. Local awareness/support status is
included when enabled. Causal quality, stale precise-claim, undiagnosable
awareness, or privacy regressions are blockers; local setup gaps are warnings.

## Note Backfill

Use `hades backend backfill-note <path> --json` to inspect old raw chunks before
turning them into project facts. The command is intentionally local and
review-only: raw chunks stay out of automatic recall, candidate facts include
evidence refs, and a human/backend review step must promote any verified fact.
For route dumps, repeated `route:* --handled_by--> file:*` edges are grouped by
handler so noisy chunks become compact candidate summaries instead of hundreds
of memory entries. Use `--create-proposals` to save candidate facts as pending
local memory proposals, then run `hades backend sync` to submit them for review.

Production release gate mapping is tracked in
[`docs/RELEASE_GATES.md`](../RELEASE_GATES.md). Use that checklist before
shipping backend MVP, PyPI, Docker, website, desktop, or update artifacts.

Self-hosted Docker production deployments should start from
[`docker-production.md`](docker-production.md) and
`docker-compose.production.yml`. The compatibility `docker-compose.yml`
host-network profile is the break-glass path, not the safe default for new
production installs.

## Observability

Hades backend sync and plugin worker paths emit sanitized structured log records
through the `hermes_cli.hades_backend` logger. In a normal CLI install these
records appear in `$HERMES_HOME/logs/agent.log`; warnings also appear in
`errors.log`.

Useful event names:

- `sync.start`, `sync.complete`, `sync.skipped`
- `sync.error`, `sync.client_error`
- `artifact.uploaded`, `artifact.skipped`
- `worker.start`, `worker.claimed`, `worker.completed`, `worker.failed`
- `doctor_report.submitted`, `doctor_report.failed`

The records include IDs, counts, status summaries, artifact schema,
truncation/redaction counts, and sanitized error text. They must not include
backend tokens, bootstrap tokens, job payload contents, lease tokens, raw source,
or local absolute paths.

For a local diagnosis, collect:

```bash
hades backend status --json
hades doctor
hades logs --level WARNING --session latest
```

When a failing test or runtime log already exists locally, turn it into typed
bug evidence before asking for a root cause:

```bash
hades backend ingest-test ./phpunit.log --bug-report-id <bug-report-id>
hades backend ingest-log ./storage/logs/laravel.log --bug-report-id <bug-report-id>
```

Both commands read a bounded excerpt, redact likely tokens/API keys/bearer
headers, extract lightweight stack frames when possible, and upload
`failing_test` or `log_excerpt` evidence to the linked backend workspace.

When diagnosing bugs through the agent, invoke the `hades-bug-diagnosis` skill
or follow the same order manually: project awareness status, existing evidence
pack search, bug evidence search, graph search, minimal source slice fetch,
evidence pack create, then a persisted structured diagnosis report with
evidence refs and confidence.

The backend and local provider enforce a hard gate for precise persisted
diagnoses: `high` or `medium` confidence reports require non-empty
`evidence_refs`, `freshness.status=current`, and
`awareness.diagnosable_without_source=true`. Otherwise the request is rejected
with `diagnosis_evidence_refs_required`, `diagnosis_freshness_not_current`, or
`diagnosis_awareness_not_diagnosable`; save a `low` or `insufficient` report
when the evidence is incomplete or stale.

Use `hades_backend_graph_search` to find candidate graph artifacts by text, then
`hades_backend_graph_traverse` when you know a starting route, URI, class,
method, file, or symbol and need bounded call-path context. Traversal results
carry freshness and artifact provenance and should be cited before making exact
route/controller/service claims without local source access.

Use `hades_backend_evidence_pack_search` before rebuilding an investigation
that may already have a current pack. Use `hades_backend_evidence_pack_create`
after collecting bug evidence refs, graph refs, and source slice ids. Evidence
packs are source-free bundles: they store refs and bounded structured payload,
not raw repository dumps, and the backend rejects unredacted secrets or payloads
over the safety limit.

After the diagnosis is verified by a passing regression test or explicit user
confirmation, promote it with the service-gated
`hades_backend_resolved_bug_promote` tool. This stores a `resolved_bug` memory
entry in backend project memory, linked to the diagnosis report and evidence,
so future similar bugs can be recalled without loading the source code.

The local no-codebase release gate is:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/agent/test_hades_bug_diagnosis_no_codebase.py
```

It verifies five complete bug fixtures, two insufficient-evidence fixtures,
evidence/tool/persistence coverage, and zero raw source/file/shell tool access.

## Artifacts

`sync_git_tree` produces `hades.git_tree.v1` artifacts with path, size, hash,
omission, retention, and truncation metadata. `project_inspection` currently
uses that same artifact schema as a metadata-only project tree inspection
(`inspection_mode=metadata_tree`); it does not synthesize an answer or include
raw source. It skips env/secrets, ignored files/directories, symlinks, generated
dependency/build directories, binary/archive files, and files above the
configured per-file budget. The artifact also carries a metadata-only
`project_index` (`hades.project_index.v1`) with language counts, detected
Laravel routes, dependency manifests, and database migration paths. The backend
memory search can retrieve this under the `artifacts` domain, so the agent can
ask about project structure without loading raw source chunks into ordinary
memory.
`populate_backend_ast` emits bounded source-symbol artifacts with provenance,
not raw source. On PHP/Laravel workspaces it produces `hades.php_graph.v1`
with detected routes, route middleware, classes, methods, Eloquent relations,
static calls with simple PHP import resolution, instantiation edges,
model-table edges, migration tables/columns/indexes/foreign keys, policy
mappings, FormRequest/request validation fields, dispatched jobs, emitted
events, event listeners, Artisan command signatures, scheduler command/job
edges, query-table edges, Eloquent query calls, and redacted config/env
references. On Node/TypeScript/React/Next/Express workspaces it produces
`hades.code_graph.v1` with framework detection, route/page handlers, symbols,
dependency manifests, and import edges. On Python workspaces it keeps the
existing `hades.symbols.v1` class/function symbol output. All artifact jobs
report omission reasons instead of following path escapes or failing the whole
sync.

`read_source_slice` is intentionally policy-gated/manual-review source access:
it reads only a bounded line window, redacts likely secrets, uploads
`retention_class=source_slice`, and does not create ordinary memory notes or
automatic prefetch context. Use it to support line-level diagnosis after bug
evidence or graph results point to a concrete file/symbol/line.

## Persephone

Persephone is the MVP realtime/inbox layer for communication between Hades
instances. It is not the primary job channel. Jobs remain pull/piggyback so
headless installations continue to work.

The first MVP contract is persistent inbox plus SSE or polling fallback. Hades
stores inbox events locally and surfaces unread/degraded state in status.
