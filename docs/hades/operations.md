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

## Sync

`hades backend sync` remains the manual repair path and bypasses any background
backoff. A successful manual sync clears stale background-sync failure state.

Normal agent turns start a lightweight piggyback sync when a profile has a
linked backend workspace and the per-profile backoff window is due. The
piggyback run is asynchronous, quiet, and fail-open: chat continues even if the
backend is offline. Repeated failures are recorded in local sync state and
surface as a degraded backend action in `hades backend status --json`.

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
