# Task 4 Fix Round 1 Report

## RED

The regression cases were added first to
`tests/hermes_cli/evolution/test_dashboard_jobs.py` and run with:

```text
scripts/run_tests.sh tests/hermes_cli/evolution/test_dashboard_jobs.py -q
```

The pre-fix run exited 1. It exposed the required behavior gaps: foreign
running overlays fabricated `finished_at`, revision-diff results accepted
unregistered shapes, configured workspace symlinks resolved through, the
default nonce survived a simulated PID change, a 101st revision-diff record
was admitted, Observer results were under-reported, shutdown could leave a
queued record, executor submission failure left durable queue state, and
ancestor symlinks/swap could redirect storage. The wrapper's aggregate count
was obscured by a stale pytest temporary-directory symlink-cleanup recursion;
the test result markers still showed the expected failing contracts.

## GREEN

All green runs used a fresh unique `--basetemp` to avoid that stale local
pytest cleanup directory:

```text
scripts/run_tests.sh tests/hermes_cli/evolution/test_dashboard_jobs.py -q -- --basetemp=<unique>
# 25 passed

scripts/run_tests.sh tests/hermes_cli/evolution/test_dashboard_service.py -q -- --basetemp=<unique>
# 39 passed

scripts/run_tests.sh tests/hermes_cli/evolution/test_p2_observer_ingestion.py -q -- --basetemp=<unique>
# 11 passed

/Users/gabriele/.hermes/hermes-agent/venv/bin/python -m py_compile hermes_cli/evolution/dashboard_jobs.py
# exit 0

git diff --check
# exit 0
```

Implemented contracts:

- All job kinds have a global, durable 100-record admission cap checked under
  the storage lock before write or executor enqueue; scans are incremental and
  stop at cap plus one.
- POSIX storage operations retain validated directory descriptors and use
  no-follow relative opens and rename; portable platforms retain a documented
  revalidation fallback. Workspace roots reject symlink traversal.
- Default process nonce is refreshed when PID changes and through the child
  fork hook.
- Submit/shutdown share a lifecycle gate; executor-submit failure becomes a
  durable terminal record rather than a stranded queued record.
- Observer results report the real bounded update count (or a truthful
  total-plus-truncated form), and durable revision diffs use an exact bounded
  public whitelist.
- Foreign queued/running overlays remain non-mutating with persisted
  `finished_at` unchanged and stable `process_interrupted` reasoning.

## Control center follow-up

The dashboard now threads the store-owned active job to `OverviewView`.
Queued or running observer scans disable **Run observer scan** and reject its
handler, while the observer pause control remains enabled. Terminal jobs
re-enable scanning. The render/interaction regression test was red before the
guard and then passed with the minimal change.

```text
npm test -- src/plugins/evolution-plugin.test.ts src/plugins/evolution-graph.test.ts src/plugins/evolution-overview-view.test.ts
# 21 passed

npm run check:evolution
# typecheck, dashboard bundle rebuild, and JavaScript syntax check passed
```
