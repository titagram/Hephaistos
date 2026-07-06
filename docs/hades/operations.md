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
`populate_backend_ast` currently has an explicit Python-only MVP scope: it
emits bounded `.py` class/function symbols with provenance, not raw source.
Both artifact jobs report omission reasons instead of following path escapes or
failing the whole sync.

## Persephone

Persephone is the MVP realtime/inbox layer for communication between Hades
instances. It is not the primary job channel. Jobs remain pull/piggyback so
headless installations continue to work.

The first MVP contract is persistent inbox plus SSE or polling fallback. Hades
stores inbox events locally and surfaces unread/degraded state in status.
