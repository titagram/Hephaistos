# API final fix — round 1

## Scope

- Extended the canonical dashboard snapshot with the effective, read-only
  Observer enablement state and a stable degradation reason.
- Made the Observer toggle return the post-mutation canonical snapshot and
  digest.
- Rejected manual Observer scans with `observer_paused` before enqueueing a
  job whenever collection is disabled.
- Added a bounded, single-ledger-snapshot overview of generations, Gnothi
  coverage and collector state, Telos revision metadata, Observer state,
  suggestion and blueprint states, and durable authorization counts.

## Integrity decisions

- Lifecycle totals and state buckets come from one `read_evolution_snapshot`
  connection; state buckets are capped at 16 and expose `truncated`.
- Public output contains prefixes, aggregates, and sanitized Telos text only;
  it does not expose full organism/request UUIDs.
- Process-local dashboard confirmations are deliberately excluded from the
  overview because including them would change the confirmation-bound snapshot
  digest.
- Missing and unavailable lifecycle data returns bounded empty/unknown
  projections instead of creating or repairing state.

## TDD evidence

The new overview, Observer-toggle, and paused-scan tests were first run red.
They respectively exposed absent coverage fields, absent Observer state/digest
refresh, and missing pre-enqueue scan gating. The degraded-reason test was
also exercised against a deliberately incorrect production value before that
value was restored.

Final verification:

```text
ruff check hermes_cli/evolution/dashboard_service.py \
  tests/hermes_cli/evolution/test_dashboard_service.py \
  tests/plugins/test_evolution_dashboard_plugin.py
All checks passed!

pytest -q [dashboard service, jobs, plugin, Observer, ledger, and reconcile suites]
270 passed in 18.87s
```

## Deferred

No dashboard UI work, source-collector changes, or Windows-specific work was
required for this API correction. Those areas remain intentionally out of this
bounded functional fix.
