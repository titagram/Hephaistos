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
Its `identity` section separates local profile memory, portable backend project
memory, and local workspace binding state so a new device can distinguish
shared project recall from source/index freshness that must be established on
that device.

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

Cleanup is dry-run by default. Add `--yes` to remove rows and
`--retention-days <days>` to override the selected local retention window for a
one-off maintenance run. `--all` includes non-expired selected candidates, but
does not delete active jobs or unreviewed refused/conflicted proposals.

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
hades backend sync
hades doctor --report-backend
```

Do not paste project bootstrap tokens, derived agent tokens, raw job payloads,
or local absolute paths into logs or support tickets.

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
- `artifact.uploaded`
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
or follow the same order manually: project awareness status, bug evidence
search, graph search, minimal source slice fetch, then a persisted structured
diagnosis report with evidence refs and confidence.

Use `hades_backend_graph_search` to find candidate graph artifacts by text, then
`hades_backend_graph_traverse` when you know a starting route, URI, class,
method, file, or symbol and need bounded call-path context. Traversal results
carry freshness and artifact provenance and should be cited before making exact
route/controller/service claims without local source access.

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
with detected routes, classes, methods, Eloquent relations, static calls, and
instantiation edges. On Python workspaces it keeps the existing
`hades.symbols.v1` class/function symbol output. Both artifact jobs report
omission reasons instead of following path escapes or failing the whole sync.

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
